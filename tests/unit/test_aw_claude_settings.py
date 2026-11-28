"""Claude Code's settings and Remote Control, layer 2's steps claude-settings and remote-control
(lib/claude_vm/guest/layer2/claude_settings.py and remote_control.py; docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), reached through B3's runner and the stand-in ssh, its
Claude Code a stand-in at the launcher that answers `claude auth status` from the login stored and settings.json (an
ANTHROPIC_API_KEY there is reported as its apiKeySource, as Claude Code reports it), or with one of today's canned
logins (tests/unit/harness.py's LOGINS); its systemctl a stand-in of this file's that keeps each unit's state where
the probe of guest.states reads it, refuses a unit whose file changed since the last daemon-reload, and, on a `tick`
of its own, does what systemd's watch of each active path unit does, by the unit's own directive: `PathChanged=`
starts the unit when the file changed, `PathExists=` whenever the file is there and the unit is not active. The units'
folder is one of the machine's own. test_remote_control_playbooks.py and test_remote_control_unit.py are unchanged and
still hold today's role.

- Their cases, carried against the steps: the canned logins Remote Control cannot use, each named with what to do;
  the settings that disable it named rather than fatal; the sign-in named as what is left unless one follows, a blocker
  where signed in; the path unit retired before Remote Control starts; a worktree spawn with no repository said; an
  empty settings.json merged into and one that is not JSON named; the units rendered as Ansible renders them, a value
  systemd would split or expand quoted as systemd reads it.
- Both directions converge: a machine running Remote Control given an API key ends with the key in settings.json and
  both units gone, said as such, the key never an argument or an event; one on the API path with the key cleared ends
  with the key removed from settings.json and Remote Control running. An apiKeySource of any kind is not eligible.
- The path unit: a machine with no login waits for one, which guest.states reads as waiting; a login landing starts
  the service through the path unit, which guest.states reads as running; a service stopped by hand is not restarted
  by it; the next run removes it and enables the service.
- A server that does not stay up fails the step, naming its journal; a second run applies nothing; the differences
  name what would change, settings by their names alone, and change nothing.
- The settings: the options each step reads are claude_code's passed through, with the role's own defaults; whether
  Remote Control is wanted is decided in one place, which aw status reads too.
- The invariant with a test of its own: the path unit watches with PathChanged=, never PathExists=.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from aw_machine import Machine, filed, machine, ssh_stand_in, target
from harness import LOGINS, REPO, _executable
from test_aw_guest_remote import _settings as _status_settings
from test_remote_control_playbooks import SETTINGS as BLOCKED
from test_remote_control_unit import VALUES, _render

from claude_vm.fleet import passthrough, paths, registry, secrets
from claude_vm.guest import states
from claude_vm.guest.layer2 import claude_settings, remote_control, step
from claude_vm.guest.layer2.claude_settings import ClaudeSettings
from claude_vm.guest.layer2.remote_control import RemoteControl
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret
from claude_vm.status import model

ROLE = REPO / "roles" / "claude_code"
SOURCES = {
    claude_settings: REPO / "lib" / "claude_vm" / "guest" / "layer2" / "claude_settings.py",
    remote_control: REPO / "lib" / "claude_vm" / "guest" / "layer2" / "remote_control.py",
}
# A canned key, never a real one.
API_KEY = "sk-ant-api03-fake-key-0000"
RELEASE = "2.1.400"
SERVICE = "claude-remote-control.service"
PATH_UNIT = "claude-remote-control.path"
STEPS = ("claude-settings", "remote-control")

# The stand-in Claude Code at the launcher: a canned login where the test left one, else the login stored, an
# ANTHROPIC_API_KEY in settings.json reported as its apiKeySource.
CLAUDE = """#!/bin/sh
[ "$1 $2" = "auth status" ] || exit 1
if [ -f "$HOME/canned-login" ]; then cat "$HOME/canned-login"; exit "$(cat "$HOME/canned-exit")"; fi
key=
if grep -q ANTHROPIC_API_KEY "$HOME/.claude/settings.json" 2>/dev/null; then
  key=', "apiKeySource": "ANTHROPIC_API_KEY"'
fi
if [ -s "$HOME/.claude/.credentials.json" ]; then
  echo "{\\"loggedIn\\": true, \\"authMethod\\": \\"claude.ai\\", \\"subscriptionType\\": \\"max\\"$key}"; exit 0
fi
if [ -n "$key" ]; then echo "{\\"loggedIn\\": true, \\"authMethod\\": \\"api_key\\"$key}"; exit 0; fi
echo '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'
exit 1
"""

# The stand-in systemctl: each unit's state in units/<unit> of the home, as `systemctl show` gives it (where
# guest.states' probe reads it), the unit files in the machine's own folder of them, every call logged.
SYSTEMCTL = f"""#!{sys.executable} -I
import hashlib, json, os, sys
home = os.environ["STAND_IN"]
units_dir = os.path.join(os.path.dirname(home), "system", "etc", "systemd", "system")
states, loaded_file = os.path.join(home, "units"), os.path.join(home, "systemd-loaded.json")
args = sys.argv[1:]
with open(os.path.join(home, "systemctl.log"), "a") as log:
    log.write(json.dumps({{"args": args, "root": "SUDO_USER" in os.environ}}) + "\\n")

def fail(said):
    sys.stderr.write(said + "\\n")
    sys.exit(1)

def digest(unit):
    path = os.path.join(units_dir, unit)
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.isfile(path) else None

def loaded():
    return json.load(open(loaded_file)) if os.path.exists(loaded_file) else {{}}

def state(unit):
    path = os.path.join(states, unit)
    if not os.path.exists(path):
        return {{}}
    return dict(line.split("=", 1) for line in open(path).read().splitlines() if "=" in line)

def save(unit, values):
    os.makedirs(states, exist_ok=True)
    with open(os.path.join(states, unit), "w") as out:
        out.write("".join(f"{{key}}={{value}}\\n" for key, value in values.items()))

def usable(unit):
    if digest(unit) is None:
        fail(f"Failed to start {{unit}}: Unit {{unit}} not found.")
    if loaded().get(unit) != digest(unit):
        fail(f"Warning: The unit file of {{unit}} changed on disk. Run 'systemctl daemon-reload' to reload units.")

def directive(unit, key):
    for line in open(os.path.join(units_dir, unit)).read().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None

def mark(path):
    try:
        found = os.stat(path)
    except FileNotFoundError:
        return None
    return [found.st_ino, found.st_mtime_ns, found.st_size, found.st_mode]

def start(unit):
    usable(unit)
    values = {{"UnitFileState": "disabled", **state(unit), "LoadState": "loaded"}}
    if unit.endswith(".path"):
        watched = directive(unit, "PathChanged") or directive(unit, "PathExists")
        values.update(ActiveState="active", SubState="waiting", Watched=json.dumps(mark(watched)))
    else:
        counter = os.path.join(home, "pids")
        pid = int(open(counter).read()) + 1 if os.path.exists(counter) else 4000
        open(counter, "w").write(str(pid))
        refused = os.path.exists(os.path.join(home, "refused"))
        values.update(
            ActiveState="activating" if refused else "active",
            SubState="auto-restart" if refused else "running",
            ExecMainPID="0" if refused else str(pid),
            NRestarts="0",
        )
    save(unit, values)

def stop(unit):
    values = state(unit)
    if values:
        values.update(ActiveState="inactive", SubState="dead", ExecMainPID="0")
        save(unit, values)

verb = args[0] if args else ""
words = [word.strip("'") for word in args[1:] if not word.startswith("-")]
flags = [word for word in args[1:] if word.startswith("-")]
if verb == "--version":
    print("systemd 255 (255.4-1ubuntu8)")
elif verb == "show":
    unit = words[0]
    values = state(unit) or {{"Id": unit, "LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead"}}
    if not state(unit) and unit in loaded():
        values.update(LoadState="loaded", UnitFileState="disabled")
    if values.get("SubState") == "auto-restart":
        values["NRestarts"] = str(int(values.get("NRestarts", "0")) + 1)
        save(unit, values)
    asked = [name for flag in flags if flag.startswith("--property=") for name in flag.split("=", 1)[1].split(",")]
    for key, value in values.items():
        if key != "Watched" and (not asked or key in asked):
            print(f"{{key}}={{value}}")
elif verb == "daemon-reload":
    names = os.listdir(units_dir) if os.path.isdir(units_dir) else []
    found = {{name: digest(name) for name in names if name.endswith((".service", ".path"))}}
    json.dump(found, open(loaded_file, "w"))
    for name in os.listdir(states) if os.path.isdir(states) else []:
        if name not in found and state(name).get("ActiveState") != "active":
            os.remove(os.path.join(states, name))
elif verb in ("enable", "disable"):
    unit = words[0]
    if verb == "enable":
        usable(unit)
    elif digest(unit) is None:
        fail(f"Failed to disable unit: Unit file {{unit}} does not exist.")
    values = {{"ActiveState": "inactive", "SubState": "dead", **state(unit), "LoadState": "loaded"}}
    values["UnitFileState"] = "enabled" if verb == "enable" else "disabled"
    save(unit, values)
    if "--now" in flags:
        start(unit) if verb == "enable" else stop(unit)
elif verb in ("start", "restart"):
    start(words[0])
elif verb == "stop":
    stop(words[0])
elif verb == "tick":
    for name in sorted(os.listdir(states)) if os.path.isdir(states) else []:
        values = state(name)
        if not name.endswith(".path") or values.get("ActiveState") != "active" or digest(name) is None:
            continue
        unit, fire = directive(name, "Unit"), False
        changed, exists = directive(name, "PathChanged"), directive(name, "PathExists")
        if changed is not None:
            now = json.dumps(mark(changed))
            fire = now != values.get("Watched")
            values["Watched"] = now
            save(name, values)
        if exists is not None and os.path.exists(exists):
            fire = True
        if fire and state(unit).get("ActiveState") != "active":
            start(unit)
else:
    fail("stand-in systemctl: unexpected arguments: " + " ".join(args))
"""


@pytest.fixture
def job(home: Path):
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


def _reached(tmp_path: Path, engine_guard, *, login: bool) -> Machine:
    """A stand-in machine alpha with Claude Code installed (the stand-in at its launcher), signed in where login, its
    key filed, the stand-in ssh first on this test's PATH, and this file's systemctl."""
    root = tmp_path / "machines"
    made = machine(root, "alpha", claude=RELEASE, login=login)
    filed(made)
    engine_guard.first_on_path(ssh_stand_in(root, [made]))
    _executable((made.home / ".local" / "bin" / "claude").resolve(), CLAUDE)
    (made.bin / "systemctl").unlink()
    _executable(made.bin / "systemctl", SYSTEMCTL)
    _units(made).mkdir(parents=True)
    return made


@pytest.fixture
def signed_in(tmp_path, engine_guard) -> Machine:
    return _reached(tmp_path, engine_guard, login=True)


@pytest.fixture
def signed_out(tmp_path, engine_guard) -> Machine:
    return _reached(tmp_path, engine_guard, login=False)


def _units(made: Machine) -> Path:
    return made.system / "etc" / "systemd" / "system"


def _settings(**over: object) -> dict[str, object]:
    return {
        "claude_code_settings": {},
        "claude_code_version": "stable",
        "claude_code_remote_control": True,
        "claude_code_remote_control_name": "alpha",
        "claude_code_remote_control_dir": "",
        "claude_code_remote_control_permission_mode": "default",
        "claude_code_remote_control_spawn": "same-dir",
        "vm_name": "alpha",
        **over,
    }


def _ctx(job: jobs.Job, made: Machine, settings, **over) -> step.Context:
    given = {"cancelled": threading.Event(), "become_exe": "sudo", **over}
    return step.Context(job, target(made), made.identity, settings, dict(os.environ), **given)


def _steps(made: Machine) -> list:
    return [ClaudeSettings(), RemoteControl(units=str(_units(made)), stay_up=0.3)]


def _run(job: jobs.Job, made: Machine, **settings: object) -> step.Ran:
    return asyncio.run(step.run_steps(_ctx(job, made, _settings(**settings)), _steps(made)))


def _heard(job: jobs.Job) -> list[jobs.Event]:
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    return heard


def _said(heard: list[jobs.Event]) -> tuple[list[str], dict[str, str]]:
    """The reports said, and each NEXT STEPS matter's last line."""
    reports = [event.text for event in heard if event.kind == "report"]
    nexts = {event.data["matter"]: event.text for event in heard if event.kind == "next"}
    return reports, nexts


def _systemctl(made: Machine, *words: str) -> None:
    """systemctl run on the machine as a person would, or the stand-in's tick: systemd's watch of the path units."""
    import subprocess

    env = {**os.environ, "STAND_IN": str(made.home)}
    subprocess.run([str(made.bin / "systemctl"), *words], check=True, env=env)


def _shown(made: Machine, unit: str) -> dict[str, str]:
    path = made.home / "units" / unit
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def _active(made: Machine, unit: str) -> str:
    """What systemd says of unit's state: inactive for one it holds nothing of."""
    return _shown(made, unit).get("ActiveState", "inactive")


def _systemctl_calls(made: Machine) -> list[dict]:
    log = made.home / "systemctl.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _remote_control(job: jobs.Job, made: Machine, settings: dict) -> str:
    """Remote Control's state as aw status says it, from guest.states' read of the machine."""
    given = _status_settings(made)
    key = claude_settings.api_key(settings)
    claude = {**given["claude"], "api_key": key is not None}
    claude["remote_control_wanted"] = remote_control.remote_control_wanted(settings)
    records = asyncio.run(
        states.read(
            job,
            target(made),
            ["claude"],
            settings={**given, "claude": claude},
            name="alpha",
            python="python3",
            env=dict(os.environ),
        )
    )
    return model.remote_control(records["claude"], time.time())[0]


def _settings_json(made: Machine) -> dict:
    return json.loads((made.home / ".claude" / "settings.json").read_text())


def _canned(made: Machine, login: str) -> None:
    status, rc = LOGINS[login]
    (made.home / "canned-login").write_text(json.dumps(status) + "\n")
    (made.home / "canned-exit").write_text(f"{rc}\n")


# ---------------------------------------------------------------------------------------------- signed in ---
def test_a_signed_in_machine_gets_remote_control_running_and_a_second_run_applies_nothing(job, signed_in):
    heard = _heard(job)
    assert _run(job, signed_in) == step.Ran("succeeded", ((STEPS[0], True), (STEPS[1], True)))
    assert _settings_json(signed_in) == {"autoUpdatesChannel": "stable"}
    assert (_units(signed_in) / SERVICE).exists() and not (_units(signed_in) / PATH_UNIT).exists()
    assert _shown(signed_in, SERVICE)["UnitFileState"] == "enabled"
    assert _remote_control(job, signed_in, _settings()) == "running"
    seeded = json.loads((signed_in.home / ".claude.json").read_text())
    projects = str(signed_in.home / "projects")
    assert seeded["projects"][projects] == {"hasTrustDialogAccepted": True} and seeded["remoteDialogSeen"] is True
    reports, nexts = _said(heard)
    assert reports == [
        'Remote Control is live for alpha as "alpha" (max plan). Open claude.ai/code or the Claude mobile app, signed'
        " in as the same account, and it appears in the session list."
    ]
    assert nexts == {"remote_control": ""}
    # As root through its sudo, every change of systemd's.
    assert all(call["root"] for call in _systemctl_calls(signed_in) if call["args"][0] != "show")
    assert _run(job, signed_in) == step.Ran("succeeded", ((STEPS[0], False), (STEPS[1], False)))


def test_a_server_that_does_not_stay_up_fails_naming_its_journal(job, signed_in):
    (signed_in.home / "refused").write_text("")
    ran = _run(job, signed_in)
    assert ran.outcome == "failed" and f"{SERVICE} did not stay running" in ran.why, ran
    assert f"journalctl -u {SERVICE} -n 40" in ran.why


def test_worktree_spawn_without_a_repository_says_so(job, signed_in):
    ran = _run(job, signed_in, claude_code_remote_control_spawn="worktree")
    assert ran.outcome == "failed" and 'claude_code_remote_control_spawn is "worktree"' in ran.why, ran
    assert "is not a git repository" in ran.why and not (_units(signed_in) / SERVICE).exists()


def test_a_value_that_is_no_single_line_is_refused_before_anything_is_written(job, signed_in):
    ran = _run(job, signed_in, claude_code_remote_control_name="alpha\nExecStartPre=/bin/false")
    assert ran.outcome == "failed" and "claude_code_remote_control_name is one line" in ran.why, ran
    assert list(_units(signed_in).iterdir()) == []


# ---------------------------------------------------------------------------------------------- an API key ---
def test_an_api_key_outranks_the_login_and_both_units_go(job, signed_in):
    assert _run(job, signed_in).outcome == "succeeded"
    _systemctl(signed_in, "stop", SERVICE)
    (_units(signed_in) / PATH_UNIT).write_text("[Path]\nPathChanged=/nowhere\n")
    heard = _heard(job)
    keyed = _settings(anthropic_api_key=Secret(API_KEY))
    ran = asyncio.run(step.run_steps(_ctx(job, signed_in, keyed), _steps(signed_in)))
    assert ran == step.Ran("succeeded", ((STEPS[0], True), (STEPS[1], True))), ran
    assert _settings_json(signed_in)["env"] == {"ANTHROPIC_API_KEY": API_KEY}
    assert list(_units(signed_in).iterdir()) == [] and _shown(signed_in, SERVICE) == {}
    assert _remote_control(job, signed_in, keyed) == "off"
    reports, nexts = _said(heard)
    assert reports == [
        "Remote Control was not set up on alpha: an Anthropic API key is configured, and ANTHROPIC_API_KEY outranks"
        " the claude.ai login that Remote Control requires, so the server would refuse to start. Clear the vault's"
        " anthropic_api_key and re-run to use Remote Control instead."
    ]
    assert nexts == {"remote_control": ""}
    # The key is the file's alone: never an argument of any call, nor in any event.
    assert API_KEY not in json.dumps(signed_in.calls()) and API_KEY not in json.dumps(_systemctl_calls(signed_in))
    assert API_KEY not in json.dumps([[event.text, dict(event.data)] for event in heard])
    assert asyncio.run(step.run_steps(_ctx(job, signed_in, keyed), _steps(signed_in))) == step.Ran(
        "succeeded", ((STEPS[0], False), (STEPS[1], False))
    )


def test_a_machine_on_the_api_path_with_the_key_cleared_ends_running_remote_control(job, signed_in):
    keyed = _settings(anthropic_api_key=Secret(API_KEY), claude_code_settings={"env": {"KEEP_ME": "1"}})
    assert asyncio.run(step.run_steps(_ctx(job, signed_in, keyed), _steps(signed_in))).outcome == "succeeded"
    assert list(_units(signed_in).iterdir()) == []
    assert _run(job, signed_in).outcome == "succeeded"
    # The key no setting manages now is removed, what else the file holds kept.
    assert _settings_json(signed_in) == {"autoUpdatesChannel": "stable", "env": {"KEEP_ME": "1"}}
    assert _remote_control(job, signed_in, _settings()) == "running"
    assert _run(job, signed_in) == step.Ran("succeeded", ((STEPS[0], False), (STEPS[1], False)))


def test_the_key_joins_the_run_s_redaction_list_before_anything_is_read(job, signed_in, monkeypatch):
    # Review 96's 6: the probe strikes the key from every event before a read of the machine can say it.
    key = Secret(API_KEY)
    read, held = claude_settings.read_file, []

    async def reading(ctx, path, mode):
        held.append(any(each is key for each in ctx.job.redacted))
        return await read(ctx, path, mode)

    monkeypatch.setattr(claude_settings, "read_file", reading)
    assert asyncio.run(step.run_steps(_ctx(job, signed_in, _settings(anthropic_api_key=key)), _steps(signed_in)))
    assert held and all(held), held


def test_an_env_of_null_is_no_env_and_the_key_no_setting_manages_still_goes():
    # Review 96's 7: claude_code_settings' env of null raised AttributeError, and the key stayed on the machine.
    on_disk = claude_settings.File(True, True, json.dumps({"env": {"A": "1", "ANTHROPIC_API_KEY": "old"}}))
    text, changes = claude_settings.planned(on_disk, {"env": None})
    assert json.loads(text) == {"env": {"A": "1"}} and changes == ("env.ANTHROPIC_API_KEY removed",)
    alone = claude_settings.File(True, True, '{"env": {"ANTHROPIC_API_KEY": "old"}}')
    text, changes = claude_settings.planned(alone, {"env": None, "theme": "dark"})
    assert json.loads(text) == {"theme": "dark"} and changes == ("env removed", "theme set")


@pytest.mark.parametrize("why", ["console", "blocker"])
def test_a_server_the_path_unit_started_that_systemd_restarts_is_stopped_once_it_may_not_run(job, signed_out, why):
    # Review 96's 1: started by the path unit, never enabled, and refused, the server is activating (auto-restart);
    # a run that finds its login of no use, or a setting in the way, stops it, as systemd_service's state=stopped does.
    assert _run(job, signed_out).outcome == "succeeded"
    (signed_out.home / "refused").write_text("")
    (signed_out.home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "never-read"}}')
    _systemctl(signed_out, "tick")
    shown = _shown(signed_out, SERVICE)
    restarting = ("activating", "auto-restart", "disabled")
    assert (shown["ActiveState"], shown["SubState"], shown["UnitFileState"]) == restarting
    given: dict[str, object] = {}
    if why == "console":
        _canned(signed_out, "console")
    else:
        given["claude_code_settings"] = {"env": {"DO_NOT_TRACK": "1"}}
    assert _run(job, signed_out, **given).outcome == "succeeded"
    assert not (_units(signed_out) / PATH_UNIT).exists()
    shown = _shown(signed_out, SERVICE)
    assert (shown["ActiveState"], shown["SubState"], shown["UnitFileState"]) == ("inactive", "dead", "disabled")
    assert ["disable", "--now", SERVICE] in [call["args"] for call in _systemctl_calls(signed_out) if call["root"]]


@pytest.mark.parametrize(
    ("login", "advice"),
    [
        ("claude-ai-with-key", 'an API key from "ANTHROPIC_API_KEY"'),
        ("console", '"claude auth logout" if it came from a Console login'),
        ("api-key", 'an API key from "ANTHROPIC_API_KEY"'),
        ("api-key-no-source", 'signed in through "api_key"'),
        ("setup-token", "CLAUDE_CODE_OAUTH_TOKEN"),
    ],
)
def test_a_login_remote_control_cannot_use_is_named_and_the_service_left_stopped(job, signed_in, login, advice):
    assert _run(job, signed_in).outcome == "succeeded"
    _canned(signed_in, login)
    heard = _heard(job)
    assert _run(job, signed_in).outcome == "succeeded"
    assert _active(signed_in, SERVICE) == "inactive"
    assert _shown(signed_in, SERVICE)["UnitFileState"] == "disabled"
    assert not (_units(signed_in) / PATH_UNIT).exists()
    reports, nexts = _said(heard)
    assert len(reports) == 1 and reports[0].startswith("Remote Control is configured on alpha but not started:")
    assert advice in reports[0], reports[0]
    # Signed in: what is left is Remote Control's, the sign-in line that checks again.
    assert nexts == {"remote_control": "aw login alpha --only claude"}


@pytest.mark.parametrize(
    "status",
    [
        {"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": "ANTHROPIC_API_KEY"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": "/login managed key"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": "anything at all"},
        {"loggedIn": True, "authMethod": "oauth_token"},
        {"loggedIn": False, "authMethod": "claude.ai"},
        {"loggedIn": "true", "authMethod": "claude.ai"},
    ],
)
def test_eligibility_rejects_any_api_key_source_and_any_other_login(status):
    assert remote_control.eligible(status, []) is False
    assert remote_control.eligible({"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": ""}, []) is True
    assert remote_control.eligible({"loggedIn": True, "authMethod": "claude.ai"}, ["DO_NOT_TRACK"]) is False


@pytest.mark.parametrize(
    ("on", "key", "wanted"),
    [(True, None, True), (True, API_KEY, False), (False, None, False), (False, API_KEY, False), ("true", None, False)],
)
def test_whether_remote_control_is_wanted_is_decided_in_one_place(on, key, wanted):
    settings = {"claude_code_remote_control": on, "anthropic_api_key": None if key is None else Secret(key)}
    assert remote_control.remote_control_wanted(settings) is wanted
    # aw status reads the same: no rule of its own.
    source = (REPO / "lib" / "claude_vm" / "cli" / "status_inputs.py").read_text()
    assert "remote_control.remote_control_wanted(" in source and '"claude_code_remote_control"] is True' not in source


# ---------------------------------------------------------------------------------------------- the path unit ---
def test_a_machine_with_no_login_waits_for_one_and_a_login_landing_starts_the_service(job, signed_out):
    heard = _heard(job)
    assert _run(job, signed_out).outcome == "succeeded"
    assert _active(signed_out, PATH_UNIT) == "active"
    assert _active(signed_out, SERVICE) == "inactive"
    assert _remote_control(job, signed_out, _settings()) == "waits"
    reports, nexts = _said(heard)
    assert "no Claude Code login is stored for dev on it." in reports[0], reports
    assert '"aw login alpha --only claude" opens the sign-in page' in reports[0]
    assert '"aw deploy alpha --only configure" then confirms it.' in reports[0]
    assert nexts == {"remote_control": "", "claude": "aw login alpha --only claude"}
    _systemctl(signed_out, "tick")
    assert _active(signed_out, SERVICE) == "inactive"
    (signed_out.home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "never-read"}}')
    _systemctl(signed_out, "tick")
    assert _remote_control(job, signed_out, _settings()) == "running"


def test_a_service_stopped_by_hand_is_not_restarted_by_the_path_unit(job, signed_out):
    assert _run(job, signed_out).outcome == "succeeded"
    (signed_out.home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "never-read"}}')
    _systemctl(signed_out, "tick")
    assert _active(signed_out, SERVICE) == "active"
    _systemctl(signed_out, "stop", SERVICE)
    for _ in range(3):
        _systemctl(signed_out, "tick")
    assert _active(signed_out, SERVICE) == "inactive"
    # The next run, signed in, retires the path unit before it starts the service, and enables that for boot.
    assert _run(job, signed_out).outcome == "succeeded"
    assert not (_units(signed_out) / PATH_UNIT).exists() and _shown(signed_out, PATH_UNIT) == {}
    assert _shown(signed_out, SERVICE)["UnitFileState"] == "enabled"
    assert _remote_control(job, signed_out, _settings()) == "running"
    calls = [call["args"] for call in _systemctl_calls(signed_out) if call["root"]]
    assert calls.index(["disable", "--now", PATH_UNIT]) < calls.index(["restart", SERVICE])


def test_a_sign_in_that_follows_leaves_the_sign_in_off_next_steps(job, signed_out):
    heard = _heard(job)
    assert _run(job, signed_out, claude_code_sign_in_follows=True).outcome == "succeeded"
    reports, nexts = _said(heard)
    assert "deploy signs it in next, in this run." in reports[0]
    assert nexts == {"remote_control": "", "claude": ""}


def test_the_path_unit_watches_with_path_changed_never_path_exists():
    for name in ("alpha", "web%h"):
        values = {**VALUES, "claude_code_remote_control_name": name}
        rendered = remote_control.render("claude-remote-control.path.j2", values)
        watched = [line for line in rendered.splitlines() if line.startswith("Path")]
        assert watched == ["PathChanged=/home/dev/.claude/.credentials.json"], rendered
    assert "PathExists" not in (ROLE / "templates" / "claude-remote-control.path.j2").read_text().split("[Unit]")[1]


# ---------------------------------------------------------------------------------------------- settings.json ---
@pytest.mark.parametrize("host", sorted(BLOCKED))
def test_settings_that_disable_remote_control_are_found_by_name(host):
    seeded, expected = BLOCKED[host]
    assert set(remote_control.blockers(seeded)) == set(expected)


def test_settings_that_disable_remote_control_are_named_rather_than_fatal(job, signed_in):
    seeded, expected = BLOCKED["everything"]
    (signed_in.home / ".claude" / "settings.json").write_text(json.dumps(seeded))
    heard = _heard(job)
    assert _run(job, signed_in).outcome == "succeeded"
    reports, nexts = _said(heard)
    named = re.search(r"disables Remote Control through (.+?)\. Each ", reports[0])
    assert named and set(named.group(1).split(", ")) == set(expected), reports
    assert "claude_code_remote_control: false" in reports[0] and "--only claude" not in reports[0]
    assert nexts == {"remote_control": "aw deploy alpha --only configure"}
    assert _active(signed_in, SERVICE) == "inactive" and not (_units(signed_in) / PATH_UNIT).exists()


@pytest.mark.parametrize("on_disk", ["", "  \n"])
def test_an_empty_settings_json_is_merged_into_rather_than_a_parse_error(job, signed_in, on_disk):
    (signed_in.home / ".claude" / "settings.json").write_text(on_disk)
    assert _run(job, signed_in, claude_code_settings={"theme": "dark"}).outcome == "succeeded"
    assert _settings_json(signed_in) == {"autoUpdatesChannel": "stable", "theme": "dark"}
    assert oct((signed_in.home / ".claude" / "settings.json").stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("on_disk", ["{not json", "[1, 2]"])
def test_a_settings_json_that_is_not_a_json_object_is_named(job, signed_in, on_disk):
    (signed_in.home / ".claude" / "settings.json").write_text(on_disk)
    ran = _run(job, signed_in)
    assert ran.outcome == "failed" and "~/.claude/settings.json on the machine is not a JSON object" in ran.why
    assert (signed_in.home / ".claude" / "settings.json").read_text() == on_disk


def test_settings_merge_as_settings_yml_merges_them():
    on_disk = claude_settings.File(True, True, json.dumps({"env": {"A": "1", "ANTHROPIC_API_KEY": "old"}, "x": [1]}))
    text, changes = claude_settings.planned(on_disk, {"env": {"B": "2"}, "x": [2]})
    assert json.loads(text) == {"env": {"A": "1", "B": "2"}, "x": [2]}
    assert changes == ("env.ANTHROPIC_API_KEY removed", "env.B set", "x changed")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    # A key no setting manages goes, and the env block with it where nothing else is left in it.
    text, changes = claude_settings.planned(claude_settings.File(True, True, '{"env": {"ANTHROPIC_API_KEY": "k"}}'), {})
    assert json.loads(text) == {} and changes == ("env removed",)
    # Nothing managed and no key named: the file is not even parsed.
    assert claude_settings.planned(claude_settings.File(True, True, "{not json"), {}) == (None, ())


def test_the_global_instructions_are_written_as_they_are(job, signed_in):
    instructions = "Use {{ braces }} as they are.\n\tAnd tabs.\n"
    assert _run(job, signed_in, claude_code_global_instructions=instructions).outcome == "succeeded"
    written = signed_in.home / ".claude" / "CLAUDE.md"
    assert written.read_text() == instructions and oct(written.stat().st_mode & 0o777) == "0o644"
    ran = _run(job, signed_in, claude_code_global_instructions=instructions)
    assert ran == step.Ran("succeeded", ((STEPS[0], False), (STEPS[1], False)))
    assert _run(job, signed_in).outcome == "succeeded" and written.read_text() == instructions


# ---------------------------------------------------------------------------------------------- the differences ---
def test_the_differences_name_what_would_change_and_change_nothing(job, signed_in):
    assert _run(job, signed_in).outcome == "succeeded"
    keyed = _settings(anthropic_api_key=Secret(API_KEY))
    ctx = _ctx(job, signed_in, keyed)
    before = _settings_json(signed_in)
    differences = []
    for each in _steps(signed_in):
        differences.append(each.difference(asyncio.run(each.probe(ctx))))
    settings, units = differences
    assert settings == step.Difference("~/.claude/settings.json", "present", "env.ANTHROPIC_API_KEY set")
    assert units.what == "Remote Control" and units.wanted == f"{SERVICE} stopped, disabled and removed"
    assert API_KEY not in json.dumps([dataclass_text(one) for one in differences])
    assert _settings_json(signed_in) == before and (_units(signed_in) / SERVICE).exists()
    assert _remote_control(job, signed_in, _settings()) == "running"


def dataclass_text(difference: step.Difference) -> list[str]:
    return [difference.what, difference.now, difference.wanted]


# ---------------------------------------------------------------------------------------------- the units ---
@pytest.mark.parametrize("name", ["alpha", "web 2", "web%h", "a,b", "$HOME"])
def test_the_units_render_as_ansible_s_template_module_renders_them(tmp_path, name):
    values = {**VALUES, "claude_code_remote_control_name": name}
    for unit, (now, _) in _render(tmp_path, values).items():
        assert remote_control.render(f"{unit}.j2", values).encode() == now, unit


# ---------------------------------------------------------------------------------------------- the settings ---
def test_the_options_read_are_claude_code_s_passed_through_with_the_role_s_own_defaults():
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    # The names the settings verb reads by: the rows, and each option passed through or refused as no setting; one a
    # row's setting reaches (`through`) or that takes any value is read by no name of its own (claude_code_user is
    # vm_user's, and only the unit's template names it).
    every = {row.name for row in registry.rows()} | {
        option.name
        for option in passthrough.options()
        if option.through is None and option.refused != passthrough.ANY_VALUE
    }
    for module, source in SOURCES.items():
        for name, default in module.OPTIONS.items():
            option = passthrough.option(name)
            assert (option.role, option.refused) == ("claude_code", None), option
            assert defaults[name] == default
        assert all(registry.row(name).name == name for name in module.ROWS)
        named = {
            node.value
            for node in ast.walk(ast.parse(source.read_text()))
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in every
        }
        run_set = set(getattr(module, "RUN_SET", ()))
        assert named - run_set == {*module.OPTIONS, *module.ROWS}, module.__name__
    assert secrets.row(claude_settings.SECRET) is not None
    blocking = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())["claude_code_remote_control_blocking_env"]
    assert list(remote_control.BLOCKING_ENV) == blocking
