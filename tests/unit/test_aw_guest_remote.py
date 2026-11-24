"""A helper verb run on a machine, and the probe's answer as found.py's states (lib/claude_vm/guest/remote.py and
guest/states.py; docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), its home made with the states of test_status_vms.py's
cases, reached through B3's runner and the stand-in ssh; the machine's python3 is the test's own Python, or a stand-in
of the test's where a machine's root has put another program in its place.

- The bounds: an answer over the size bound refused whole; a verb out of its time ended with its tree (proc.py's) and
  the lines it sent before kept; nothing said within its time, and an address no machine has, not reached; a value with
  a control character, a key too, made one plain line; NaN, Infinity, a number past a float and a nesting past the
  bound refused; a machine whose python3 does not run the bootstrap refused.
- The invariant with a test of its own: no word of the verb's, nothing it is asked, nothing of its standard input and
  no password is ever on the argument list of the ssh child, and none of them is in any file of the machine's.
- sudo, by N-23 section 13, point 5: -n first; a sudo that asks nothing never receives the password and its function is
  never called; one that asks is sent it once, with -S, and a wrong one is not tried again; a refusal for another
  reason sends none; a sudo password is a Secret, and a Secret never follows the helper as the verb's input.
- The states: states.read gives found.py's records, the ones today's status makes of today's probe's document on the
  same machine, for the parts asked and no others; Tailscale's read as root, and as the user with root's part said not
  read where sudo refuses; and a layer-2 step asks whether Claude is signed in, in a Python of its own, with no Ansible
  loaded.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pwd
import shlex
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_kit import environment, searched_for
from aw_machine import Machine, filed, machine, ssh_stand_in, target
from harness import REPO, _executable
from test_status_vms import CASES, EDITORS

from claude_vm import proc
from claude_vm.fleet import paths
from claude_vm.guest import bundle, remote, states
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret
from claude_vm.ssh import runner
from claude_vm.status import found, reach

# The bootstrap's first line, as a machine prints it.
STARTED = bundle.STARTED
# A machine's sudo password, and another.
RIGHT = "the-right-one"
WRONG = "not-the-right-one"
# One machine's settings in the shape status keeps (status/settings.py's VM): the lines' commands are words of the
# test's own.
SETTINGS = {
    "claude": {
        "method": "native",
        "api_key": False,
        "remote_control_wanted": True,
        "unit": "claude-remote-control",
        "login_command": "LOGIN-CLAUDE",
    },
    "github": {
        "user": "dev",
        "auth": True,
        "auth_method": "auto",
        "token_wanted": False,
        "token_set": False,
        "configure_command": "CONFIGURE",
        "login_command": "LOGIN-GITHUB",
        "workflow_asked": False,
    },
    "tailscale": {
        "wanted": True,
        "hostname": "alpha",
        "ssh": False,
        "accept_routes": False,
        "accept_dns": True,
        "advertise_tags": [],
        "login_server": "",
        "auth_method": "auto",
        "key_wanted": False,
        "key_refused_locally": False,
        "joined_tag": "agent-warehouse-tailscale",
        "admin_page": "ADMIN",
        "configure_command": "CONFIGURE",
        "login_command": "LOGIN-TAILSCALE",
    },
    "vpn": "tailscale",
    "projects_dir": "",
}


@pytest.fixture
def job(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "status", "status alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


def _asked(made: Machine) -> dict:
    """What today's just status asks a machine's probe for, in status/settings.py's ASKED shape, for a stand-in: every
    read, but apt's simulation, which would run this computer's apt."""
    home = made.home
    return {
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "limit": 25,
        "system": {"root": str(made.system), "upgrades": False, "timeout": 5, "editors": EDITORS, "known": {}},
        "claude": {"unit": "claude-remote-control"},
        "github": {
            "marker": str(home / ".config" / "agent-warehouse" / "gh-login"),
            "marker_1x": str(home / ".config" / "claude-on-proxmox" / "gh-login"),
        },
        "work": {"dir": str(home / "projects")},
        "tailscale": {
            "wanted": True,
            "foreign": [str(home / "foreign" / "tailscaled")],
            "marker": str(home / "markers" / "tailscale-installed"),
            "marker_1x": "",
            "joined": str(home / "markers" / "tailscale-joined"),
            "joined_1x": "",
        },
    }


def _settings(made: Machine) -> dict:
    return {**SETTINGS, "asked": _asked(made)}


def _reached(tmp_path: Path, engine_guard, **state) -> Machine:
    """A stand-in machine alpha made for state, its key filed, and the stand-in ssh first on this test's PATH."""
    root = tmp_path / "machines"
    made = machine(root, "alpha", **{**CASES["ready"], **state})
    filed(made)
    engine_guard.first_on_path(ssh_stand_in(root, [made]))
    return made


def _direct(at: runner.Target) -> runner.Target:
    """at marked as a route settled straight at it marks one (connections.target_for): a Target made for an address no
    target() gives, which the runner connects to only marked."""
    return dataclasses.replace(at, routed_as="direct")


def _helper(job: jobs.Job, made: Machine, verb: str = "probe", **given) -> remote.Outcome:
    """verb run on made through the helper, made's own python3 and sudo named by name, as a stand-in takes them."""
    given = {"as_root": False, "python": "python3", "become_exe": "sudo", "env": dict(os.environ), **given}
    return asyncio.run(remote.helper(job, target(made), verb, **given))


def _python3(made: Machine, script: str) -> None:
    """made's python3 put in place by its root: script, a shell script, which never runs the bootstrap."""
    (made.bin / "python3").unlink()
    _executable(made.bin / "python3", f"#!/bin/sh\ncat > /dev/null\n{script}")


# ---------------------------------------------------------------- a verb's answer ----


def test_a_verb_s_answer_is_its_lines_parsed_after_the_bootstrap_s(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    answer = _helper(job, alpha, asked=_asked(alpha))
    assert isinstance(answer, remote.Answer) and (answer.status, answer.said) == (0, ""), answer
    started, document = answer.lines
    assert started == {"started": True} and set(document) == {"user", "root"}, answer.lines
    assert document["user"]["claude"]["login"] is True and document["root"]["tailscale"]["package"] == "installed"
    # As the machine's user: no sudo at all.
    assert alpha.sudo_calls() == [] and len(alpha.calls()) == 1


def test_a_value_with_a_control_character_reaches_the_caller_as_one_plain_line(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    (alpha.system / "etc" / "os-release").write_text(
        'PRETTY_NAME="\x1b]0;a title\x07Ubuntu\x1b[31m 24.04\t\tLTS\x7f\x9b2J"\n'
    )
    answer = _helper(job, alpha, asked={"system": _asked(alpha)["system"]})
    assert isinstance(answer, remote.Answer), answer
    assert answer.lines[-1]["user"]["system"]["os"] == "Ubuntu 24.04 LTS", answer.lines[-1]


def test_a_key_too_is_made_one_plain_line(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    _python3(
        alpha, f"""printf '%s\\n' '{STARTED}' '{{"a\\u001b[31m\\nb": ["c\\u0007d", {{"e\\u2028f": "g\\th"}}]}}'\n"""
    )
    answer = _helper(job, alpha)
    assert answer == remote.Answer(0, ({"a b": ["cd", {"e f": "g h"}]},), ""), answer


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", '"x" ]', "[" * 70 + "]" * 70])
def test_nan_infinity_and_what_is_no_json_are_refused_whole(tmp_path, engine_guard, job, value) -> None:
    alpha = _reached(tmp_path, engine_guard)
    _python3(alpha, f"printf '%s\\n' '{STARTED}' '{{\"fine\": 1}}' '{{\"load\": {value}}}'\n")
    answer = _helper(job, alpha)
    assert isinstance(answer, remote.NotAnswered) and answer.reached and answer.lines == (), answer
    assert answer.why.startswith(("it sent a line that is not JSON", "it nests deeper than 64 levels")), answer.why


def test_loads_refuses_what_today_s_status_refuses() -> None:
    for line in ("NaN", "Infinity", "-Infinity", "1e999", '{"a": [1, NaN]}', '{"a": 1e400}'):
        with pytest.raises(ValueError):
            reach.loads(line)
        with pytest.raises(ValueError):
            remote.loads(line)
    for line in ('{"a": [1, 2.5, -3e10, "x", null, true]}', "0", '"s"'):
        assert remote.loads(line) == reach.loads(line)


def test_an_answer_over_the_size_bound_is_refused_whole(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    answer = _helper(job, alpha, asked=_asked(alpha), limits=runner.Limits(line=512))
    assert answer == remote.NotAnswered("it sent a line longer than 512 bytes", reached=True), answer
    answer = _helper(job, alpha, asked=_asked(alpha), limits=runner.Limits(most=1024))
    assert answer == remote.NotAnswered("it sent more than 1024 bytes", reached=True), answer


def test_a_verb_out_of_its_time_is_ended_with_its_tree(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    pids = alpha.directory / "hanging.pids"
    # gh hangs: it says its own pid and the probe's, its parent, and waits.
    (alpha.bin / "gh").unlink()
    _executable(alpha.bin / "gh", f'#!/bin/sh\necho "$$ $PPID" > "{pids}"\nexec sleep 300\n')
    ssh: list = []
    tree: list[int] = []

    async def run() -> remote.Outcome:
        async def watch() -> None:
            while not pids.exists() or not pids.read_text().endswith("\n"):
                await asyncio.sleep(0.05)
            tree.extend(proc.descendants(ssh[0].pid))

        watching = asyncio.ensure_future(watch())
        try:
            return await remote.helper(
                job,
                target(alpha),
                "probe",
                asked={"github": _asked(alpha)["github"]},
                as_root=False,
                python="python3",
                env=dict(os.environ),
                limits=runner.Limits(seconds=4),
                started=ssh.append,
            )
        finally:
            await asyncio.wait_for(watching, 10)

    answer = asyncio.run(run())
    assert answer == remote.NotAnswered("it took longer than 4 s", reached=True, lines=({"started": True},)), answer
    gh, probe = (int(word) for word in pids.read_text().split())
    assert gh in tree and probe in tree, (gh, probe, tree)
    left = list(tree)
    until = time.monotonic() + 10
    while left and time.monotonic() < until:
        left = [pid for pid in left if _alive(pid)]
        time.sleep(0.05)
    assert left == [], f"left running of the verb's tree: {left}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_nothing_said_within_its_time_is_not_reached(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    (alpha.bin / "python3").unlink()
    _executable(alpha.bin / "python3", "#!/bin/sh\nexec sleep 300\n")
    answer = _helper(job, alpha, limits=runner.Limits(silent=1, seconds=20))
    assert answer == remote.NotAnswered("it took the connection and said nothing for 1 s: timed out", reached=False)


def test_an_address_no_machine_has_is_not_reached(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    elsewhere = _direct(
        runner.machine(
            alpha.identity,
            "203.0.113.200",
            alpha.account,
            "/aw-machines/cp",
            known_hosts=alpha.directory.parent / "known_hosts",
        )
    )
    outcome = asyncio.run(remote.helper(job, elsewhere, "probe", as_root=False, python="python3", env=dict(os.environ)))
    assert isinstance(outcome, remote.NotAnswered) and not outcome.reached, outcome
    assert "No route to host" in outcome.why, outcome


def test_a_python3_that_does_not_run_the_bootstrap_is_refused(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    _python3(alpha, "printf '%s\\n' '{\"user\": {}}'\n")
    answer = _helper(job, alpha)
    assert answer == remote.NotAnswered(
        "it did not answer as the helper does: its first line is not the bootstrap's", reached=True
    )
    answer = _helper(job, alpha, python="no-such-python")
    assert isinstance(answer, remote.NotRun) and not answer.sudo and "no-such-python" in answer.said, answer


def test_a_verb_it_does_not_take_is_the_dispatcher_s_one_line(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    answer = _helper(job, alpha, "nosuch", stdin=b"\nnever said = (\n")
    assert answer == remote.Answer(2, (), 'helper: no verb "nosuch": the verbs are probe'), answer


# ---------------------------------------------------------------- nothing on the command line ----


def test_no_word_nothing_asked_nothing_of_its_input_and_no_password_is_on_ssh_s_command_line(
    tmp_path, engine_guard, job
) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    canaries = ["canary-verb", "canary-word", "canary-asked", "canary-input", RIGHT]
    answer = _helper(
        job,
        alpha,
        "canary-verb",
        words=("canary-word",),
        asked={"canary-asked": "canary-asked"},
        stdin=b"canary-input\n",
        as_root=True,
        password=lambda: Secret(RIGHT),
    )
    assert answer == remote.Answer(2, (), 'helper: no verb "canary-verb": the verbs are probe'), answer
    # Two connections: sudo -n, refused for want of the password, then -S with it.
    calls = alpha.calls()
    assert len(calls) == 2 and [entry["n"] for entry in alpha.sudo_calls()] == [True, False], (
        calls,
        alpha.sudo_calls(),
    )
    words = [word for call in calls for word in [*call["argv"], *call["env"]]]
    assert [word for word in words if any(canary in word for canary in canaries)] == [], words
    assert searched_for(canaries, alpha.directory) == []
    # The command line is the machine's interpreter given the bootstrap, as root by sudo in the C locale, alone.
    assert calls[0]["argv"][-1] == runner.argv(target(alpha), remote.command("python3", "sudo"))[-1]
    assert calls[1]["argv"][-1] == runner.argv(target(alpha), remote.command("python3", "sudo", password=True))[-1]


def test_a_secret_never_follows_the_helper_as_its_input(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    for given in (Secret("never-sent"), "text", [b"a", b"b"]):
        with pytest.raises(runner.Refused, match=r"\Aa verb's standard input is bytes, never a Secret"):
            _helper(job, alpha, stdin=given)
    assert alpha.calls() == []
    # A password is a Secret: a plain string given for one is refused once sudo -n has asked for it, and never sent.
    plain: object = RIGHT
    with pytest.raises(runner.Refused, match=r"\Aa sudo password is a Secret\Z"):
        _helper(job, alpha, asked={}, as_root=True, password=lambda: plain)
    assert [entry["n"] for entry in alpha.sudo_calls()] == [True] and len(alpha.calls()) == 1


# ---------------------------------------------------------------- sudo ----


def test_a_sudo_that_asks_nothing_never_receives_the_password(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    asked: list[int] = []
    answer = _helper(job, alpha, asked={}, as_root=True, password=lambda: asked.append(1) or Secret(RIGHT))
    assert isinstance(answer, remote.Answer) and answer.status == 0, answer
    assert asked == [] and alpha.sudo_calls() == [{"n": True, "S": False, "read": 0, "outcome": "ran"}]
    assert len(alpha.calls()) == 1


def test_a_sudo_that_asks_is_sent_the_password_once_after_its_refusal(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    asked: list[int] = []
    answer = _helper(job, alpha, asked={}, as_root=True, password=lambda: asked.append(1) or Secret(RIGHT))
    assert isinstance(answer, remote.Answer) and answer.status == 0, answer
    assert asked == [1] and alpha.sudo_calls() == [
        {"n": True, "S": False, "read": 0, "outcome": "refused: a password is required"},
        {"n": False, "S": True, "read": 1, "outcome": "ran"},
    ]


def test_a_wrong_password_is_sent_once_and_never_again(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    asked: list[int] = []
    answer = _helper(job, alpha, asked={}, as_root=True, password=lambda: asked.append(1) or Secret(WRONG))
    assert isinstance(answer, remote.NotRun) and answer.sudo, answer
    assert answer.said.endswith("sudo: 3 incorrect password attempts"), answer
    # One -S, never a second: sudo itself read the lines after the password as its next two attempts.
    assert asked == [1] and alpha.sudo_calls() == [
        {"n": True, "S": False, "read": 0, "outcome": "refused: a password is required"},
        {"n": False, "S": True, "read": 3, "outcome": "refused: a wrong password"},
    ]
    assert len(alpha.calls()) == 2


@pytest.mark.parametrize("password", [None, lambda: None], ids=["no function", "none held"])
def test_with_no_password_held_sudo_s_refusal_is_said(tmp_path, engine_guard, job, password) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    answer = _helper(job, alpha, asked={}, as_root=True, password=password)
    assert answer == remote.NotRun("sudo: a password is required", sudo=True), answer
    assert [entry["n"] for entry in alpha.sudo_calls()] == [True] and len(alpha.calls()) == 1


def test_a_refusal_for_another_reason_sends_no_password(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    asked: list[int] = []
    answer = _helper(
        job, alpha, asked={}, as_root=True, python="no-such-python", password=lambda: asked.append(1) or Secret(RIGHT)
    )
    assert answer == remote.NotRun("sudo: no-such-python: command not found", sudo=True), answer
    assert asked == [] and len(alpha.calls()) == 1


# ---------------------------------------------------------------- the states ----


def _today_s_records(job: jobs.Job, made: Machine, parts: frozenset[str]) -> dict:
    """What today's status makes of today's probe on made, sent through the runner as reach.py sends it, as the
    machine's user: found.ssh_found of its last document, for the sections that ask for parts."""
    asked = states.asked(_asked(made), parts)
    result = asyncio.run(
        runner.run(
            job,
            target(made),
            [str(made.home / "python"), "-I", "-B", "-c", reach.BOOTSTRAP],
            reach.probe_script(asked),
            runner.LIMITS,
            dict(os.environ),
        )
    )
    assert isinstance(result, runner.Done) and result.status == 0, result
    sections = sorted({states.SECTIONS[part] for part in parts})
    return found.ssh_found(reach.loads(result.lines[-1]), _settings(made), "alpha", "", sections, False)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_states_are_found_s_records_of_today_s_probe_for_the_parts_asked(tmp_path, engine_guard, job, name):
    alpha = _reached(tmp_path, engine_guard, **CASES[name])
    parts = frozenset({"system", "claude", "github", "work"})
    records = asyncio.run(
        states.read(
            job, target(alpha), parts, settings=_settings(alpha), name="alpha", python="python3", env=dict(os.environ)
        )
    )
    today = _today_s_records(job, alpha, parts)
    assert records == {
        **today,
        "tailscale": None,
        "vpn": None,
        "unread": {k: v for k, v in today["unread"].items() if k in parts},
    }
    assert records["reached"] and records["claude"]["login_stored"] is CASES[name]["login"]
    # As the machine's user alone: no sudo for a read of the user's.
    assert alpha.sudo_calls() == []


def test_a_part_asked_alone_is_the_one_read(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    records = asyncio.run(
        states.read(
            job,
            target(alpha),
            ["claude"],
            settings=_settings(alpha),
            name="alpha",
            python="python3",
            env=dict(os.environ),
        )
    )
    assert records["claude"]["login_stored"] is True and records["claude"]["version"] == "2.1.310"
    assert [part for part in states.PARTS if records[part] is not None] == ["claude"]
    assert records["unread"] == {"claude": ""} and records["vpn"] is None


def test_what_a_machine_answers_beyond_the_parts_asked_is_left_out(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    whole = _helper(job, alpha, asked=_asked(alpha))
    assert isinstance(whole, remote.Answer) and set(whole.lines[-1]["user"]) == {"system", "claude", "github", "work"}
    # Its root's python3 answers every part, whatever it was asked: only the part asked is read.
    said = [STARTED, *(json.dumps(line) for line in whole.lines)]
    _python3(alpha, f"printf '%s\\n' {' '.join(shlex.quote(line) for line in said)}\n")
    records = asyncio.run(
        states.read(
            job,
            target(alpha),
            ["claude"],
            settings=_settings(alpha),
            name="alpha",
            python="python3",
            env=dict(os.environ),
        )
    )
    assert [part for part in states.PARTS if records[part] is not None] == ["claude"], records
    assert records["vpn"] is None and records["unread"] == {"claude": ""}, records


def test_tailscale_is_read_as_root(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    # Tailscale's state is its daemon's, whoever asks; the stand-in keeps it under HOME, which sudo makes root/.
    (alpha.root_home / ".tailscale-fake").symlink_to(alpha.home / ".tailscale-fake")
    records = asyncio.run(
        states.read(
            job,
            target(alpha),
            ["tailscale"],
            settings=_settings(alpha),
            name="alpha",
            python="python3",
            become_exe="sudo",
            env=dict(os.environ),
        )
    )
    today = _today_s_records(job, alpha, frozenset({"tailscale"}))
    assert records["tailscale"] == today["tailscale"] and records["vpn"] == today["vpn"], records
    assert records["tailscale"]["installed"] and records["tailscale"]["ours"], records["tailscale"]
    assert [entry["n"] for entry in alpha.sudo_calls()] == [True]


def test_where_sudo_refuses_the_user_s_reads_stand_and_root_s_part_is_said_not_read(tmp_path, engine_guard, job):
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    records = asyncio.run(
        states.read(
            job,
            target(alpha),
            ["claude", "tailscale"],
            settings=_settings(alpha),
            name="alpha",
            python="python3",
            become_exe="sudo",
            env=dict(os.environ),
        )
    )
    assert records["claude"]["login_stored"] is True and records["tailscale"] is None, records
    assert records["unread"] == {"claude": "", "tailscale": "sudo refused: sudo: a password is required"}, records
    # sudo -n once, then the user's reads with no sudo.
    assert [entry["n"] for entry in alpha.sudo_calls()] == [True] and len(alpha.calls()) == 2


def test_a_machine_not_reached_is_said_so(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    elsewhere = _direct(
        runner.machine(
            alpha.identity,
            "203.0.113.200",
            alpha.account,
            "/aw-machines/cp",
            known_hosts=alpha.directory.parent / "known_hosts",
        )
    )
    records = asyncio.run(
        states.read(
            job, elsewhere, ["claude"], settings=_settings(alpha), name="alpha", python="python3", env=dict(os.environ)
        )
    )
    assert not records["reached"] and "No route to host" in records["unreachable"] and records["claude"] is None


def test_the_parts_are_the_probe_s(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    for parts in ([], ["claude", "nosuch"], "claude"):
        with pytest.raises(runner.Refused, match=r"\Athe parts read are"):
            asyncio.run(
                states.read(job, target(alpha), parts, settings=_settings(alpha), name="alpha", env=dict(os.environ))
            )
    assert alpha.calls() == []


# A layer-2 step, in a Python of its own: is Claude signed in on the machine? What it found, and what of Ansible it
# loaded.
LAYER_2 = """
import asyncio, dataclasses, json, os, sys
given = json.loads(sys.argv[1])
sys.path.insert(0, given["lib"])
from pathlib import Path
from claude_vm.fleet import paths
from claude_vm.guest import states
from claude_vm.runs import job as jobs
from claude_vm.ssh import runner


async def signed_in():
    job = jobs.create(paths.jobs_dir(paths.home(Path(given["repo"]))), "deploy", "deploy alpha", None, ["alpha"],
                      {"kind": "terminal"}, True)
    try:
        at = runner.machine(given["identity"], given["address"], given["account"], given["control"],
                            known_hosts=given["known_hosts"])
        # Marked as a route settled straight at it marks one (connections.target_for).
        at = dataclasses.replace(at, routed_as="direct")
        records = await states.read(job, at, ["claude"], settings=given["settings"], name="alpha", python="python3",
                                    env=dict(os.environ))
    finally:
        jobs.drop(job)
    return records["claude"]["login_stored"]


answer = asyncio.run(signed_in())
print(json.dumps({"signed_in": answer, "ansible": sorted(m for m in sys.modules if m.split(".")[0] == "ansible")}))
"""


@pytest.mark.parametrize("name", ["ready", "fresh"])
def test_a_layer_2_step_asks_whether_claude_is_signed_in_with_no_ansible_loaded(tmp_path, home, name) -> None:
    root = tmp_path / "machines"
    alpha = machine(root, "alpha", **CASES[name])
    filed(alpha)
    given = {
        "lib": str(REPO / "lib"),
        "repo": str(REPO),
        "identity": alpha.identity,
        "address": alpha.address,
        "account": alpha.account,
        "control": "/aw-machines/cp",
        "known_hosts": str(alpha.directory.parent / "known_hosts"),
        "settings": _settings(alpha),
    }
    ran = subprocess.run(
        [sys.executable, "-I", "-c", LAYER_2, json.dumps(given)],
        env=environment(home, first_on_path=[ssh_stand_in(root, [alpha])]),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr[-3000:]
    assert json.loads(ran.stdout) == {"signed_in": CASES[name]["login"], "ansible": []}
    assert [call["outcome"] for call in alpha.calls()] == ["ran"] and alpha.sudo_calls() == []
