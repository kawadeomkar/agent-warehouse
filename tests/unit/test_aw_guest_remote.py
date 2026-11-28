"""A helper verb run on a machine, and the probe's answer as found.py's states (lib/claude_vm/guest/remote.py and
guest/states.py; docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), its home made with the states of test_status_vms.py's
cases, reached through B3's runner and the stand-in ssh; the machine's python3 is the test's own Python, or a stand-in
of the test's where a machine's root has put another program in its place.

- The bounds: an answer over the size bound, or past the bound on its lines, refused whole within the call's time; its
  parse ended within the call's time; a verb out of its time ended with its tree (proc.py's) and the lines it sent
  before kept; nothing said within its time, and an address no machine has, not reached; a value with a control
  character, a key too, made one plain line; NaN, Infinity, a number past a float and a nesting past the bound refused;
  a machine whose python3 does not run the bootstrap refused; what a shell prints at login before the bootstrap's line
  dropped, and the answer read whole.
- The invariant with a test of its own: no word of the verb's, nothing it is asked, nothing of its standard input and
  no password is ever on the argument list of the ssh child, and none of them is in any file of the machine's.
- sudo, by N-23 section 13, point 5: -n first; a sudo that asks nothing never receives the password and its source is
  never asked; one that asks, in sudo's words or sudo-rs's, is sent it once, with -S, its line alone and the script
  only once the program runs; a wrong one costs one attempt and is sent to no machine again in the run, which says so
  once, however its session ended (by itself, by the call's time, with none to say anything in) and however many calls
  were under way side by side; a refusal for another reason sends none; a sudo password is a Secret, given as the
  run's Password, and a Secret never follows the helper as the verb's input; and a password a machine says back - on
  its standard error as it read it, in JSON's escapes, as a C string and a shell's printf %q, cut by the bound five
  bytes in; as a string, escaped, a key or a number - is in no field of the outcome, of the states' record, or of the
  job's folder, a password with a quote, a control character or a space at its end too.
- The states: states.read gives found.py's records, the ones today's status makes of today's probe's document on the
  same machine, for the parts asked and no others; Tailscale's read as root, and as the user with root's part said not
  read where sudo refuses, within what is left of the call's time; and a layer-2 step asks whether Claude is signed in,
  in a Python of its own, with no Ansible loaded.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
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

from claude_vm import proc, text
from claude_vm.fleet import paths
from claude_vm.guest import bundle, remote, states
from claude_vm.runs import job as jobs
from claude_vm.secret import MARK, Secret, struck_end
from claude_vm.ssh import runner
from claude_vm.status import found, reach

# The bootstrap's first line, as a machine prints it.
STARTED = bundle.STARTED
# A sudo password of the test's, a canary: digits, which a machine can say back as a JSON number; words past ASCII,
# which it can say back escaped; a quote and a backslash, which JSON, a C string and a shell put a backslash before; a
# control character, which the runner drops from what it reads; and a space at its end, which it strips (review 70's 2).
CANARIES = ("8675309024", "canary-pässwörd", 'canary"quote\\slash-ü', "canary\x01control", "canary-trailing ")
# The bound on what a machine says on its standard error for the cut's case: a stand-in says so much before the
# password that the bound falls five bytes into it.
CUT = 4096
# How a sudo of a test's hands a call on to the machine's own stand-in sudo (aw_machine's), given the machine's
# directory.
HANDED_ON = (
    'os.execv(sys.executable, [sys.executable, "-I", os.path.join(os.path.dirname(sys.argv[1]), ".programs",'
    ' "sudo.py"), *sys.argv[1:]])'
)
# sudo-rs under -n where it wants a password, by the name @NAME@ it says itself by; every other call the machine's own.
SUDO_RS = f"""
import os, sys
if "-n" in sys.argv[2:]:
    sys.stderr.write("@NAME@: interactive authentication is required\\n")
    sys.exit(1)
{HANDED_ON}
"""
# A sudo that says back the line it read for a password: on its standard error as it read it, in JSON's escapes, as
# a C string and, where it is printable, as bash's printf %q writes it; and where it runs (@MODE@ answers), after the
# bootstrap's line as JSON: a string as it is, a string escaped, a key, every character a \u escape, which only the
# parse reads back, and a number where the line is digits. Or it refuses (exit 1), ends as ssh does where it loses a
# machine (255), or (cut) refuses after so much on its standard error that the bound CUT falls five bytes into the
# line it read.
ECHOING_SUDO = r"""
import json, os, subprocess, sys
if "-n" in sys.argv[2:]:
    sys.stderr.write("sudo: a password is required\n")
    sys.exit(1)
got = b""
while not got.endswith(b"\n") and (byte := os.read(0, 1)):
    got += byte
word = got.decode().rstrip("\n")
if "@MODE@" == "cut":
    sys.stderr.buffer.write(b"x" * (@CUT@ - 5) + got)
    sys.exit(1)
c = "".join("\\" + character if character in '"\\' else character for character in word)
said = [f"sudo: it read {word}", f"sudo: as JSON {json.dumps(word)} and {json.dumps(word, ensure_ascii=False)}",
        f'sudo: as C "{c}"']
if word.isprintable():
    quoted = subprocess.run(["/bin/bash", "-c", 'printf %q "$1"', "bash", word], capture_output=True, text=True,
                            env={"LC_ALL": "C.UTF-8"}, check=True)
    said.append(f"sudo: as a shell {quoted.stdout}")
sys.stderr.buffer.write("".join(line + "\n" for line in said).encode())
sys.stderr.flush()
if "@MODE@" != "answers":
    sys.exit(1 if "@MODE@" == "refuses" else 255)
lines = [@STARTED@, json.dumps({"raw": word}, ensure_ascii=False), json.dumps({"escaped": word}),
         json.dumps({word: 1}, ensure_ascii=False), '{"spelt": "' + "".join("\\u%04x" % ord(c) for c in word) + '"}']
if word.isdigit():
    lines.append('{"number": ' + word + '}')
sys.stdout.buffer.write("".join(line + "\n" for line in lines).encode())
sys.stdout.flush()
sys.stdin.buffer.read()
"""
# A sudo that, under -S, reads the password's line, notes the send in sent.log beside the machine, and then neither
# reads nor ends: its session ends by the call's time alone. Under -n, sudo's refusal for want of a password.
HANGING_SUDO = r"""
import os, sys, time
if "-n" in sys.argv[2:]:
    sys.stderr.write("sudo: a password is required\n")
    sys.exit(1)
while os.read(0, 1) not in (b"", b"\n"):
    pass
with open(os.path.join(sys.argv[1], "sent.log"), "a") as out:
    out.write("sent\n")
time.sleep(60)
"""
# A sudo that, under -S, reads the password's line, waits, and says whether anything was already there after it; then
# the bootstrap's line, and it keeps what follows, which a program would read. Every other call the machine's own.
PEEKING_SUDO = f"""
import hashlib, json, os, select, sys, time
if "-S" not in sys.argv[2:]:
    {HANDED_ON}
got = b""
while not got.endswith(b"\\n") and (byte := os.read(0, 1)):
    got += byte
time.sleep(0.5)
ahead = bool(select.select([0], [], [], 0)[0])
sys.stdout.write(@STARTED@ + "\\n")
sys.stdout.flush()
rest = sys.stdin.buffer.read()
with open(os.path.join(sys.argv[1], "peeked.json"), "w") as out:
    out.write(json.dumps({{"line": hashlib.sha256(got).hexdigest(), "ahead": ahead}}))
with open(os.path.join(sys.argv[1], "rest.bin"), "wb") as out:
    out.write(rest)
"""
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
    target() gives, which the runner connects to only marked, with what the mark was decided from."""
    return dataclasses.replace(at, routed_as="direct", routed_from=runner.decided_from(at))


def _helper(job: jobs.Job, made: Machine, verb: str = "probe", **given) -> remote.Outcome:
    """verb run on made through the helper, made's own python3 and sudo named by name, as a stand-in takes them."""
    given = {"as_root": False, "python": "python3", "become_exe": "sudo", "env": dict(os.environ), **given}
    return asyncio.run(remote.helper(job, target(made), verb, **given))


def _python3(made: Machine, script: str) -> None:
    """made's python3 put in place by its root: script, a shell script, which never runs the bootstrap."""
    (made.bin / "python3").unlink()
    _executable(made.bin / "python3", f"#!/bin/sh\ncat > /dev/null\n{script}")


def _sudo(made: Machine, source: str, **values: str) -> None:
    """made's sudo put in place by its root: source, a Python program of the test's, each @NAME@ in it values' NAME
    (STARTED too, the bootstrap's line), run with made's directory and then sudo's own words."""
    for name, value in {"STARTED": repr(STARTED), **values}.items():
        source = source.replace(f"@{name}@", value)
    program = made.directory / "sudo-stand-in.py"
    program.write_text(source)
    (made.bin / "sudo").unlink()
    _executable(made.bin / "sudo", f'#!/bin/sh\nexec "{sys.executable}" -I "{program}" "{made.directory}" "$@"\n')


def _strings(value: object) -> Iterator[str]:
    """Every string value holds - an outcome's fields, a record's keys and values - however deep."""
    if isinstance(value, str):
        yield value
    elif dataclasses.is_dataclass(value):
        for field in dataclasses.fields(value):
            yield from _strings(getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _forms_of(canary: str) -> set[str]:
    """What of canary a machine says back, in each form ECHOING_SUDO says it and the runner reads it, and its first five
    characters, where a cut falls: none of it may be in what a caller is given."""
    c = "".join(f"\\{character}" if character in '"\\' else character for character in canary)
    return {
        canary,
        canary.strip(),
        text.plain_text(canary).strip(),
        json.dumps(canary)[1:-1],
        json.dumps(canary, ensure_ascii=False)[1:-1],
        c,
        canary[:5],
    }


def _said_back(canary: str) -> str:
    """What ECHOING_SUDO says on its standard error for canary, each form of it struck, one line."""
    said = f'sudo: it read {MARK} sudo: as JSON "{MARK}" and "{MARK}" sudo: as C "{MARK}"'
    return f"{said} sudo: as a shell {MARK}" if canary.isprintable() else said


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
    assert answer == remote.NotRun(
        "it did not answer as the helper does: no line of the bootstrap's (exit 0)", sudo=False
    ), answer
    answer = _helper(job, alpha, python="no-such-python")
    assert isinstance(answer, remote.NotRun) and not answer.sudo and "no-such-python" in answer.said, answer


def test_a_verb_it_does_not_take_is_the_dispatcher_s_one_line(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    answer = _helper(job, alpha, "nosuch", stdin=b"\nnever said = (\n")
    assert answer == remote.Answer(2, (), 'helper: no verb "nosuch": the verbs are probe'), answer


def test_what_a_shell_prints_at_login_is_dropped_and_the_answer_read_whole(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    # Lines on its standard output, one of them JSON, and a word on its standard error: as a .bashrc may say them above
    # its check that the shell is interactive.
    (alpha.home / ".bashrc").write_text('echo "Welcome, dev"\necho \'{"user": {}}\'\necho "a word at login" >&2\n')
    answer = _helper(job, alpha, asked={"claude": _asked(alpha)["claude"]})
    assert isinstance(answer, remote.Answer) and (answer.status, answer.said) == (0, "a word at login"), answer
    assert answer.lines[0] == {"started": True} and answer.lines[-1]["user"]["claude"]["login"] is True, answer.lines
    # As root: sudo -n's refusal read past the shell's words, the password asked and sent, the answer read whole.
    asked: list[int] = []
    password = remote.Password(lambda: asked.append(1) or Secret(RIGHT))
    answer = _helper(job, alpha, asked={}, as_root=True, password=password)
    assert isinstance(answer, remote.Answer) and (answer.status, answer.said) == (0, "a word at login"), answer
    assert asked == [1] and alpha.sudo_calls() == [
        {"n": True, "S": False, "read": 0, "outcome": "refused: a password is required"},
        {"n": False, "S": True, "read": 1, "outcome": "ran"},
    ]


def test_an_answer_past_the_bound_on_lines_is_refused_whole_within_the_call_s_time(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    # Short lines past LINES, each of them JSON, far within every bound in bytes.
    _python3(alpha, f"printf '%s\\n' '{STARTED}'\nyes 1 | head -n {remote.LINES * 25}\n")
    began = time.monotonic()
    answer = _helper(job, alpha, limits=runner.Limits(seconds=10))
    assert answer == remote.NotAnswered(f"it sent more than {remote.LINES} lines", reached=True), answer
    assert time.monotonic() - began < 10
    # A caller's own bound, the bootstrap's line one of the lines it counts.
    _python3(alpha, f"printf '%s\\n' '{STARTED}'\nyes 1 | head -n 10\n")
    assert _helper(job, alpha, limits=runner.Limits(lines=11)) == remote.Answer(0, (1,) * 10, "")
    answer = _helper(job, alpha, limits=runner.Limits(lines=10))
    assert answer == remote.NotAnswered("it sent more than 10 lines", reached=True), answer


def test_the_parse_of_an_answer_ends_within_the_call_s_time(tmp_path, engine_guard, job, monkeypatch) -> None:
    alpha = _reached(tmp_path, engine_guard)
    _python3(alpha, f"printf '%s\\n' '{STARTED}'\nyes '{{\"n\": 1}}' | head -n 40\n")
    loads = remote.loads

    def slow(line: str) -> object:
        # A line that takes a quarter of a second to parse: the forty of them, ten seconds.
        time.sleep(0.25)
        return loads(line)

    monkeypatch.setattr(remote, "loads", slow)
    began = time.monotonic()
    answer = _helper(job, alpha, limits=runner.Limits(seconds=3))
    took = time.monotonic() - began
    assert answer == remote.NotAnswered("it took longer than 3 s", reached=True), answer
    # Within the call's time, to within one line's parse.
    assert took < 3 + 1.5, took


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
        password=remote.Password(lambda: Secret(RIGHT)),
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
        _helper(job, alpha, asked={}, as_root=True, password=remote.Password(lambda: plain))
    assert [entry["n"] for entry in alpha.sudo_calls()] == [True] and len(alpha.calls()) == 1
    # And it is the run's Password, made once for the run: a function alone is refused before anything starts.
    with pytest.raises(runner.Refused, match=r"\Aa sudo password is the run's Password \(remote\.Password\)"):
        _helper(job, alpha, asked={}, as_root=True, password=lambda: Secret(RIGHT))
    with pytest.raises(runner.Refused, match=r"\Aa sudo password is made of a function that gives it"):
        remote.Password(Secret(RIGHT))
    assert len(alpha.calls()) == 1


# ---------------------------------------------------------------- sudo ----


def test_a_sudo_that_asks_nothing_never_receives_the_password(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    asked: list[int] = []
    answer = _helper(
        job, alpha, asked={}, as_root=True, password=remote.Password(lambda: asked.append(1) or Secret(RIGHT))
    )
    assert isinstance(answer, remote.Answer) and answer.status == 0, answer
    assert asked == [] and alpha.sudo_calls() == [{"n": True, "S": False, "read": 0, "outcome": "ran"}]
    assert len(alpha.calls()) == 1


def test_a_sudo_that_asks_is_sent_the_password_once_after_its_refusal(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    asked: list[int] = []
    answer = _helper(
        job, alpha, asked={}, as_root=True, password=remote.Password(lambda: asked.append(1) or Secret(RIGHT))
    )
    assert isinstance(answer, remote.Answer) and answer.status == 0, answer
    assert asked == [1] and alpha.sudo_calls() == [
        {"n": True, "S": False, "read": 0, "outcome": "refused: a password is required"},
        {"n": False, "S": True, "read": 1, "outcome": "ran"},
    ]


def test_a_wrong_password_costs_one_attempt_and_is_sent_to_no_machine_again_in_the_run(tmp_path, engine_guard, job):
    root = tmp_path / "machines"
    alpha, beta = (machine(root, name, **CASES["ready"], sudo_password=RIGHT) for name in ("alpha", "beta"))
    for made in (alpha, beta):
        filed(made)
    engine_guard.first_on_path(ssh_stand_in(root, [alpha, beta]))
    asked: list[int] = []
    password = remote.Password(lambda: asked.append(1) or Secret(WRONG))
    # sudo is given the time to say anything to run the program with it: 3 s here.
    limits = runner.Limits(silent=3)
    answer = _helper(job, alpha, asked={}, as_root=True, password=password, limits=limits)
    assert isinstance(answer, remote.NotRun) and answer.sudo, answer
    assert answer.said.endswith(f"sudo: 1 incorrect password attempt - {remote.NOT_TAKEN}"), answer
    # One -S, one attempt: the password's line alone, and nothing more until the program runs, which it never did.
    assert asked == [1] and password.refused
    assert alpha.sudo_calls() == [
        {"n": True, "S": False, "read": 0, "outcome": "refused: a password is required"},
        {"n": False, "S": True, "read": 1, "outcome": "refused: no password"},
    ]
    # Said once: the run's next calls, on the machine and on another, take sudo -n's refusal and send nothing.
    for made in (alpha, beta):
        again = _helper(job, made, asked={}, as_root=True, password=password, limits=limits)
        assert again == remote.NotRun("sudo: a password is required", sudo=True), again
    assert asked == [1] and [entry["S"] for entry in alpha.sudo_calls()] == [False, True, False]
    assert beta.sudo_calls() == [{"n": True, "S": False, "read": 0, "outcome": "refused: a password is required"}]
    assert [len(made.calls()) for made in (alpha, beta)] == [3, 1]


def test_the_password_s_line_goes_alone_and_the_script_only_once_the_program_runs(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    _sudo(alpha, PEEKING_SUDO)
    answer = _helper(job, alpha, asked={}, as_root=True, password=remote.Password(lambda: Secret(RIGHT)))
    assert answer == remote.Answer(0, (), ""), answer
    # sudo read the password's line, and nothing was there after it until the program said it ran; then the script,
    # whole, and nothing of the password's.
    peeked = json.loads((alpha.directory / "peeked.json").read_text())
    assert peeked == {"line": hashlib.sha256(f"{RIGHT}\n".encode()).hexdigest(), "ahead": False}, peeked
    assert (alpha.directory / "rest.bin").read_bytes() == bundle.script(["probe"], {}).encode()


@pytest.mark.parametrize("held", [False, True], ids=["no password", "none held"])
def test_with_no_password_held_sudo_s_refusal_is_said(tmp_path, engine_guard, job, held) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    password = remote.Password(lambda: None) if held else None
    answer = _helper(job, alpha, asked={}, as_root=True, password=password)
    assert answer == remote.NotRun("sudo: a password is required", sudo=True), answer
    assert [entry["n"] for entry in alpha.sudo_calls()] == [True] and len(alpha.calls()) == 1


def test_a_refusal_for_another_reason_sends_no_password(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    asked: list[int] = []
    answer = _helper(
        job,
        alpha,
        asked={},
        as_root=True,
        python="no-such-python",
        password=remote.Password(lambda: asked.append(1) or Secret(RIGHT)),
    )
    assert answer == remote.NotRun("sudo: no-such-python: command not found", sudo=True), answer
    assert asked == [] and len(alpha.calls()) == 1


@pytest.mark.parametrize("name", ["sudo-rs", "sudo"])
def test_sudo_rs_s_refusal_for_want_of_a_password_is_read_as_sudo_s(tmp_path, engine_guard, job, name) -> None:
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    _sudo(alpha, SUDO_RS, NAME=name)
    asked: list[int] = []
    password = remote.Password(lambda: asked.append(1) or Secret(RIGHT))
    answer = _helper(job, alpha, asked={}, as_root=True, password=password)
    assert isinstance(answer, remote.Answer) and answer.status == 0, answer
    # Asked once and sent: the -S call is the machine's own stand-in's, which took it and ran the program.
    assert asked == [1] and alpha.sudo_calls() == [{"n": False, "S": True, "read": 1, "outcome": "ran"}]
    assert len(alpha.calls()) == 2


@pytest.mark.parametrize("canary", CANARIES, ids=["digits", "past ascii", "quote", "control", "trailing"])
def test_the_password_a_machine_says_back_is_in_no_field_of_the_outcome(tmp_path, engine_guard, job, canary) -> None:
    alpha = _reached(tmp_path, engine_guard)
    outcomes = []
    for mode in ("answers", "refuses", "unreachable", "cut"):
        _sudo(alpha, ECHOING_SUDO, MODE=mode, CUT=str(CUT))
        limits = runner.Limits(said=CUT) if mode == "cut" else runner.LIMITS
        password = remote.Password(lambda: Secret(canary))
        outcomes.append(_helper(job, alpha, asked={}, as_root=True, password=password, limits=limits))
    forms = _forms_of(canary)
    assert [said for said in _strings(outcomes) if any(form in said for form in forms)] == []
    assert searched_for([canary], job.folder) == []
    answers, refuses, unreachable, cut = outcomes
    # Its mark where it was said back: on the standard error, in each form, as read; in a line before it is parsed; in
    # a string after; and at the end of what the bound cut five bytes into it.
    said = _said_back(canary)
    if canary.isdigit():
        # Said back as a number: struck from the line, which is then no JSON, and the answer refused whole.
        assert isinstance(answers, remote.NotAnswered) and answers.reached, answers
        assert answers.why.startswith("it sent a line that is not JSON"), answers
    else:
        spelt = ({"raw": MARK}, {"escaped": MARK}, {MARK: 1}, {"spelt": MARK})
        assert answers == remote.Answer(0, spelt, said), answers
    assert refuses == remote.NotRun(f"{said} - {remote.NOT_TAKEN}", sudo=True), refuses
    # Sent, and no line of the bootstrap's came: marked refused, however the session ended (review 70's 1).
    assert unreachable == remote.NotAnswered(f"{said} - {remote.NOT_TAKEN}", reached=False), unreachable
    assert cut == remote.NotRun(f"{'x' * (CUT - 5)}{MARK} - {remote.NOT_TAKEN}", sudo=True), cut


def test_an_end_a_cut_left_partway_through_the_password_is_struck_and_no_other_end() -> None:
    secrets = (Secret("canary-pw"), Secret("xab"), Secret("ab"))
    assert struck_end("x" * 10 + "cana", secrets) == "x" * 10 + MARK
    # A cut inside a character leaves a replacement character, struck with the part before it; the longest end of any.
    assert struck_end("said canary-p\ufffd", secrets) == f"said {MARK}"
    assert struck_end("zxa", secrets) == f"z{MARK}"
    assert struck_end("said nothing of it", secrets) == "said nothing of it"
    with pytest.raises(TypeError):
        struck_end("x", ["canary-pw"])
    # The helper strikes such an end only where what was said came near its bound, which a cut leaves it at.
    forms = remote._forms(Secret("canary-pw"))
    near, far = (runner.Done(1, (), "x" * size + "cana") for size in (4096 - remote.CUT_NEAR, 100))
    assert remote._struck(near, forms, 4096).said == "x" * (4096 - remote.CUT_NEAR) + MARK
    assert remote._struck(far, forms, 4096).said == "x" * 100 + "cana"


def test_a_wrong_password_is_marked_refused_however_its_session_ends(tmp_path, engine_guard, job) -> None:
    # Review 70's 1: where the call's time was under the time to say anything, or there was none, the session ran to its
    # deadline, the password was not marked, and every call of the run sent it again.
    alpha = _reached(tmp_path, engine_guard, sudo_password=RIGHT)
    limits = runner.Limits(seconds=6, silent=None)
    password = remote.Password(lambda: Secret(WRONG))
    began = time.monotonic()
    answer = _helper(job, alpha, asked={}, as_root=True, password=password, limits=limits)
    took = time.monotonic() - began
    # Its standard input closed REFUSAL_GRACE before the call's end: sudo read its end and said its own refusal in time.
    assert isinstance(answer, remote.NotRun) and answer.sudo, answer
    assert answer.said.endswith(f"sudo: 1 incorrect password attempt - {remote.NOT_TAKEN}"), answer
    assert password.refused and took < 6, took
    # A sudo that never ends once it has the line: the session ends by the call's time, and is marked all the same.
    _sudo(alpha, HANGING_SUDO)
    hanging = remote.Password(lambda: Secret(WRONG))
    began = time.monotonic()
    answer = _helper(job, alpha, asked={}, as_root=True, password=hanging, limits=limits)
    took = time.monotonic() - began
    assert answer == remote.NotAnswered(f"it took longer than 6 s - {remote.NOT_TAKEN}", reached=True), answer
    assert hanging.refused and took < 6 + 2.5, took
    # Each sent once in the run: the next calls take sudo -n's refusal and send nothing.
    for given in (password, password, hanging, hanging):
        again = _helper(job, alpha, asked={}, as_root=True, password=given, limits=limits)
        assert again == remote.NotRun("sudo: a password is required", sudo=True), again
    assert [entry["S"] for entry in alpha.sudo_calls()] == [False, True]
    assert (alpha.directory / "sent.log").read_text() == "sent\n"


def _side_by_side(job: jobs.Job, machines: list[Machine], password: remote.Password, limits: runner.Limits) -> list:
    """One helper call as root on each of machines, all started at once, their outcomes in the same order."""

    async def calls() -> list:
        return await asyncio.gather(
            *(
                remote.helper(
                    job,
                    target(made),
                    "probe",
                    asked={},
                    as_root=True,
                    password=password,
                    limits=limits,
                    python="python3",
                    become_exe="sudo",
                    env=dict(os.environ),
                )
                for made in machines
            )
        )

    return asyncio.run(calls())


def test_calls_side_by_side_send_a_wrong_password_once_in_all(tmp_path, engine_guard, job) -> None:
    # Review 70's 4: each call under way sent it, four attempts on one machine in one run, each saying "sent to no
    # machine again". The run's first send settles it; the others wait for it, then read it refused.
    root = tmp_path / "machines"
    alpha, beta = (machine(root, name, **CASES["ready"], sudo_password=RIGHT) for name in ("alpha", "beta"))
    for made in (alpha, beta):
        filed(made)
    engine_guard.first_on_path(ssh_stand_in(root, [alpha, beta]))
    asked: list[int] = []
    password = remote.Password(lambda: asked.append(1) or Secret(WRONG))
    outcomes = _side_by_side(job, [alpha, alpha, alpha, alpha, beta, beta], password, runner.Limits(silent=3))
    assert asked == [1] and password.refused
    sent = [entry for made in (alpha, beta) for entry in made.sudo_calls() if entry["S"]]
    assert sent == [{"n": False, "S": True, "read": 1, "outcome": "refused: no password"}], sent
    said = [outcome for outcome in outcomes if remote.NOT_TAKEN in repr(outcome)]
    assert len(said) == 1 and said[0].said.endswith(f"1 incorrect password attempt - {remote.NOT_TAKEN}"), outcomes
    assert [outcome for outcome in outcomes if outcome not in said] == [
        remote.NotRun("sudo: a password is required", sudo=True)
    ] * 5
    # A right one, side by side: settled by the first, then sent by each, its source asked once.
    asked.clear()
    password = remote.Password(lambda: asked.append(1) or Secret(RIGHT))
    outcomes = _side_by_side(job, [alpha, alpha, beta, beta], password, runner.Limits(silent=3))
    assert all(isinstance(outcome, remote.Answer) and outcome.status == 0 for outcome in outcomes), outcomes
    assert asked == [1] and not password.refused
    ran = [entry for made in (alpha, beta) for entry in made.sudo_calls() if entry["S"] and entry["outcome"] == "ran"]
    assert len(ran) == 4, ran


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


def test_the_read_after_sudo_s_refusal_is_given_what_is_left_of_the_call_s_time(tmp_path, engine_guard, job) -> None:
    alpha = _reached(tmp_path, engine_guard)
    # sudo takes 3 s to refuse for want of a password; the user's read then never ends.
    _sudo(alpha, 'import sys, time\ntime.sleep(3)\nsys.stderr.write("sudo: a password is required\\n")\nsys.exit(1)\n')
    _python3(alpha, "exec sleep 30\n")
    began = time.monotonic()
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
            limits=runner.Limits(seconds=6, silent=None),
        )
    )
    took = time.monotonic() - began
    # 6 s in all: the user's read given the 3 left, never 6 more, which would take 9.
    assert took < 6 + 2.5, took
    assert records["unread"]["claude"].startswith("it took longer than"), records
    assert len(alpha.calls()) == 2


@pytest.mark.parametrize("canary", CANARIES[1:3], ids=["past ascii", "quote"])
def test_the_password_a_machine_says_back_is_in_no_field_of_the_states_record(tmp_path, engine_guard, job, canary):
    alpha = _reached(tmp_path, engine_guard)
    _sudo(alpha, ECHOING_SUDO, MODE="refuses", CUT=str(CUT))
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
            password=remote.Password(lambda: Secret(canary)),
        )
    )
    forms = _forms_of(canary)
    assert [said for said in _strings(records) if any(form in said for form in forms)] == []
    assert searched_for([canary], job.folder) == []
    assert records["claude"]["login_stored"] is True and records["tailscale"] is None, records
    # JSON's form of it among what it said, struck as the others are (review 70's 2).
    assert records["unread"]["tailscale"] == f"sudo refused: {_said_back(canary)} - {remote.NOT_TAKEN}", records


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
        at = dataclasses.replace(at, routed_as="direct", routed_from=runner.decided_from(at))
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
