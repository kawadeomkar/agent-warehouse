"""A VM configured by the new engine, the frame: lib/claude_vm/engine/configure.py, and deploy's branch --only configure
in lib/claude_vm/engine/deploy.py (card G4; docs/architecture/engine.md §16.4).

Without the fake: the parts a configure asks for and runs (Plan), a part of layer 2's refused naming G9, the stage a
record takes (finished only once one configure ran all six of today's parts), the home read from getent's line, and the
failures heard from layer 1's events.

On a whole fleet folder against the fake Proxmox, each VM made first by G3's `aw deploy --only provision` and reached
at a stand-in machine (tests/unit/aw_machine.py), its key filed under its identity. Through `aw deploy` as a person runs
it: a stopped VM left stopped and never reached; a VM with no key filed refused before any connection, with the line
that files it; a --tags naming a part of layer 2 refused naming G9, before anything is read; a VM the fleet may not
change refused before any connection where it is named, and left out and said where none is. In this process, through
the frame, the system play swapped for a play of one task a part that touches only the stand-in's own directory and run
over F3's test-only local connection (F30's layer1_host), which the test alone hands layer 1 (the real roles converge
only in F6's molecule/system/ scenario, never here): a VM left by G3 has its three parts run, stays unfinished with the
three of layer 2 named, its digest empty and nothing written, its user's home and workspace read, every connection to it
by its identity, its key checked strictly and the person's vm_ssh_options given; --tags common runs that part alone and
--tags with no names finds every VM (tests/unit/test_configure_tags.py's case); a failed task fails the VM naming the
task, cloud-init's in this engine's lines; a callback of the product's name beside the play, which ran in its place,
refused; every secret the run holds struck from what layer 1 says; a stopped VM started where the plan says. And, over
the stand-in's real SSH connection (as G7 proved its route), a configure through the stand-in hop reaches the VM only
through it, and with the hop's login refused stops before the VM.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import shutil
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from aw_kit import run_aw
from aw_machine import LOOPBACK, SSH_FOLDER, Machine, hop, hop_modes, layer1_host
from fleet_fixture import ROUTE
from harness import REPO, _pve
from pki import proxmox_pki
from test_aw_create import FIRST, SECOND, Made, _making, _ok, _open
from test_aw_routes import _set

from claude_vm.engine import configure, deploy, fleet, routes
from claude_vm.engine.record import Record
from claude_vm.fleet import hosts, paths
from claude_vm.guest import layer1, system
from claude_vm.hosts.model import Request
from claude_vm.pve import record as block
from claude_vm.runs import job as jobs
from claude_vm.runs import model, rundir
from claude_vm.schema.rules import CONFIGURE_ROLES
from claude_vm.schema.words import EXITS

CONFIGURE_PY = REPO / "lib" / "claude_vm" / "engine" / "configure.py"
# The UUID of a VM no stand-in is: a record that names it is a copy of another VM's notes.
OTHER_UUID = "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0"


# ---------------------------------------------------------------- without the fake ----


def test_a_configure_asks_every_part_unless_tags_name_some_and_runs_layer_1_s_alone() -> None:
    every = configure.Plan.read()
    assert every.parts == CONFIGURE_ROLES
    assert every.system_parts == system.PARTS == ("common", "vscode_server", "dev_tools")
    assert every.unrun == ("claude_code", "github_projects", "tailscale") == configure.LAYER_2
    # Named in any order: in today's order, the play's parts alone run.
    named = configure.Plan.read("dev_tools,common")
    assert (named.parts, named.system_parts, named.unrun) == (("common", "dev_tools"), ("common", "dev_tools"), ())
    assert configure.Plan.read("common", True).start is True and configure.Plan.read("common", "yes").start is False
    with pytest.raises(ValueError):
        configure.Plan.read("nginx")


def test_a_part_of_layer_2_is_refused_naming_g9_and_layer_1_s_are_not() -> None:
    for tags in ("claude_code", "common,tailscale", "github_projects"):
        sentence = configure.unbuilt(tags)
        assert sentence is not None and "card G9" in sentence and sentence.startswith("error: --tags "), sentence
        assert "aw deploy --only configure --tags common,vscode_server,dev_tools" in sentence
    assert configure.unbuilt("common,vscode_server,dev_tools") is None and configure.unbuilt(None) is None


def test_the_stage_is_finished_only_once_one_configure_ran_all_six_parts() -> None:
    assert configure.stage_after(system.PARTS) is None
    assert configure.stage_after(("common",)) is None and configure.stage_after(()) is None
    assert configure.stage_after(CONFIGURE_ROLES) == "finished"
    assert configure.stage_after(tuple(reversed(CONFIGURE_ROLES))) == "finished"


def test_the_home_is_getent_s_sixth_field_an_absolute_path() -> None:
    assert configure.home_of(["dev:x:1000:1000:dev:/home/dev:/bin/bash"], "dev") == "/home/dev"
    assert configure.home_of(["devs:x:1:1::/srv/devs:/bin/sh", "dev:x:2:2::/opt/dev:/bin/sh"], "dev") == "/opt/dev"
    for lines in ([], ["dev:x:1000:1000:dev:home/dev:/bin/bash"], ["dev:x:1000"], ["other:x:1:1::/home/o:/bin/sh"]):
        with pytest.raises(ValueError):
            configure.home_of(lines, "dev")


def _event(kind: str, machine: str | None, **data: object) -> jobs.Event:
    return jobs.Event(0.0, kind, machine, "t", data)


def test_the_failures_heard_are_layer_1_s_failed_tasks_by_machine() -> None:
    heard = configure.Failures()
    one, two = "agent-warehouse-" + OTHER_UUID, "agent-warehouse-" + OTHER_UUID.replace("0f", "1f", 1)
    heard(_event("step", one, status="failed", task="Check the disk", message="full"))
    # An ignored failure, an item's, a verb's own step, and another kind of event: none is the machine's failure.
    heard(_event("step", two, status="failed", task="ignored", message="", ignored=True))
    heard(_event("step", two, status="failed", task="an item", message="", item="x"))
    heard(_event("step", two, status="failed", stage="configure"))
    heard(_event("report", two, status="failed", task="a report"))
    heard(_event("step", two, status="unreachable", task="Gather", message="no route"))
    assert heard.found == {one: ("Check the disk", "full"), two: ("Gather", "no route")}


def test_only_a_vm_that_succeeded_gives_its_home_and_workspace_for_its_alias() -> None:
    target = SimpleNamespace(name="delta", identity="agent-warehouse-" + OTHER_UUID)
    read = configure.Configuring(None, None, None, configure.Plan(), target, configure.Failures())
    read.stage, read.home, read.workspace = "unfinished", "/home/dev", "/home/dev/projects"
    done = read.outcome("succeeded")
    assert (done.outcome, done.stage, done.home, done.workspace, done.ok) == (
        "succeeded",
        "unfinished",
        "/home/dev",
        "/home/dev/projects",
        True,
    )
    for outcome in ("failed", "cancelled", "left stopped"):
        ended = read.outcome(outcome)
        assert (ended.home, ended.workspace, ended.ok) == ("", "", outcome == "left stopped"), outcome


def test_configure_names_no_test_connection_nor_a_first_use() -> None:
    text = CONFIGURE_PY.read_text()
    for name in ("test_connection", "test_collections", "first_use", "runner.machine", "ansible_"):
        assert name not in text, name


# ---------------------------------------------------------------- against the fake ----


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made(home, tmp_path, pki) -> Iterator[Made]:
    """A whole fleet against the fake, as test_aw_create.py's: the template in the pool, this computer's key in its
    home, the guests' agents answering at once."""
    yield from _making(home, tmp_path, pki, True)


def _provisioned(made: Made, *names: str) -> dict[str, tuple[int, Machine]]:
    """names made by G3's provision, each at a stand-in machine: by name, its VMID and its machine."""
    machines = [made.stand_in(f"m{index}", vmid) for index, vmid in enumerate((FIRST, SECOND)[: len(names)])]
    _ok(made.deploy(*names))
    found = {}
    for vmid, each in zip((FIRST, SECOND), machines, strict=False):
        record = made.record(vmid)
        assert (record.stage, record.provisioned, record.converged) == ("unfinished", True, "")
        found[record.name] = (vmid, each)
    return found


def _aw(made: Made, *words: str):
    """aw deploy <words> as a person runs it, the stand-in ssh first on its PATH."""
    return run_aw("deploy", *words, home=made.home, first_on_path=(made.root / SSH_FOLDER,), timeout=180)


def _said(ran) -> str:
    """What a run said, its lines as the live view wraps them joined again."""
    return " ".join((ran.stdout + ran.stderr).split())


def test_a_stopped_vm_is_left_stopped_and_never_reached(made) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    _pve(made.base, "POST", f"nodes/pve/qemu/{vmid}/status/stop")
    calls, sent = len(machine.calls()), len(made.sent())
    ran = _aw(made, "delta", "--only", "configure")
    _ok(ran)
    assert 'VM "delta" (VMID 9001) is stopped, so it was left as it is and not configured' in _said(ran)
    assert "aw deploy delta --only provision --start" in _said(ran)
    assert made.status(vmid) == "stopped" and made.writes(sent) == [] and len(machine.calls()) == calls


def test_a_vm_with_no_key_filed_is_refused_before_any_connection_with_the_line_that_files_it(made) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    filed = made.home / ".ssh" / "agent-warehouse.known_hosts"
    filed.write_text("".join(line + "\n" for line in made.filed() if line != machine.line))
    calls, sent = len(machine.calls()), len(made.sent())
    ran = _aw(made, "delta", "--only", "configure")
    _ok(ran, EXITS["failed"])
    assert f"no host key of {machine.identity} is filed" in _said(ran)
    assert "aw deploy delta --only provision" in _said(ran)
    assert len(machine.calls()) == calls and made.writes(sent) == []


def test_tags_naming_a_part_of_layer_2_is_refused_naming_g9_before_anything_is_read(made) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    calls, sent = len(machine.calls()), len(made.sent())
    for words in (("delta", "--tags", "claude_code"), ("--only", "configure", "--tags", "common,tailscale")):
        ran = _aw(made, *words)
        _ok(ran, EXITS["refused"])
        assert "card G9" in _said(ran) and "error: --tags " in _said(ran)
    assert len(machine.calls()) == calls and len(made.sent()) == sent


def test_a_vm_the_fleet_may_not_change_is_refused_named_and_left_out_unnamed_before_any_connection(made) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    # Its notes a copy of another VM's: a record that names another UUID, provisioned as G3 leaves one.
    copy = Record(kind=fleet.KIND, uuid=OTHER_UUID, name="delta", stage="unfinished", provisioned=True)
    _pve(made.base, "PUT", f"nodes/pve/qemu/{vmid}/config", description=block.render("", copy.fields()))
    calls, sent = len(machine.calls()), len(made.sent())
    ran = _aw(made, "delta", "--only", "configure")
    _ok(ran, EXITS["refused"])
    assert f"error: the record in VM {vmid}'s notes" in _said(ran) and "never changed or deleted" in _said(ran)
    ran = _aw(made, "--only", "configure")
    _ok(ran)
    assert f"Left out: the record in VM {vmid}'s notes" in _said(ran), _said(ran)
    assert len(machine.calls()) == calls and made.writes(sent) == []


# ---------------------------------------------------------------- the frame, in this process ----

# The play a test runs in system.yml's place: one task a part, each tagged with its part, touching a file of its own in
# the machine's home by a module's path option, which the module expands on the machine.
TOUCHED = "ran-{part}"


def _tasks() -> list[dict]:
    return [
        {
            "name": f"Converge {part}",
            "ansible.builtin.file": {"path": f"~/{TOUCHED.format(part=part)}", "state": "touch", "mode": "0600"},
            "tags": [part],
        }
        for part in system.PARTS
    ]


@pytest.fixture
def job(made) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that configures has; gone after."""
    made_job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy delta", "pve", ["delta"], {"kind": "terminal"}, True
    )
    rundir.make(made_job)
    yield made_job
    rundir.remove(made_job)
    jobs.drop(made_job)


@pytest.fixture
def heard(job) -> list[jobs.Event]:
    """Every event the run emits."""
    found: list[jobs.Event] = []
    job.listen(found.append)
    return found


class Swapped:
    """The system play swapped for a play of the test's own, in its own folder, which layer 1 takes the play's names
    for; and layer 1's run wrapped to hand it F3's test-only local connection on the machine its target names - the
    test's alone to give - with every call it was given kept."""

    def __init__(self, monkeypatch, folder: Path, local: bool) -> None:
        self.folder = folder
        folder.mkdir(parents=True)
        self.play = folder / "system.yml"
        self.write(_tasks())
        monkeypatch.setattr(system, "PLAY", self.play)
        monkeypatch.setattr(layer1, "PLAY_NAMES", {**layer1.PLAY_NAMES, self.play: system.NAMES})
        self.machines: dict[str, Machine] = {}
        # A machine that does not answer is given up on in seconds here, not SSHD's three minutes.
        monkeypatch.setattr(configure, "SSHD", 5.0)
        monkeypatch.setattr(configure, "PAUSE", 0.2)
        self.calls: list[dict] = []
        real = layer1.run

        async def run(job, target, play, variables_file=None, **given):
            assert "test_connection" not in given and "test_collections" not in given, given
            self.calls.append({"target": target, "play": play, "variables": variables_file, **given})
            if local:
                given["test_connection"] = layer1_host(self.machines[target.identity])
            return await real(job, target, play, variables_file, **given)

        monkeypatch.setattr(layer1, "run", run)

    def write(self, tasks: list[dict]) -> None:
        self.play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": tasks}]))


@pytest.fixture
def swapped(monkeypatch, tmp_path, installed, made, engine_guard) -> Swapped:
    """The play swapped and layer 1 over the local connection; the stand-in ssh first on this process's PATH."""
    engine_guard.first_on_path(made.root / SSH_FOLDER)
    return Swapped(monkeypatch, tmp_path / "play", True)


def _configured(
    names: tuple[str, ...], plan: configure.Plan, job: jobs.Job, short: Path | None = None
) -> tuple[configure.Configured, ...]:
    """The frame run in this process, against the fleet's host as a run opens it: its environment's HOME short where
    given, short enough for a shared connection's socket in its .ansible/cp."""
    where = paths.home(REPO)

    async def body() -> tuple[configure.Configured, ...]:
        ctx = SimpleNamespace(
            job=job, cancelled=asyncio.Event(), stopping=threading.Event(), emit=job.emit, shield=_open
        )
        with fleet.connect(where, hosts.reach(hosts.Layout(where), Request())) as opened:
            environ = opened.environ if short is None else {**opened.environ, "HOME": str(short)}
            host = dataclasses.replace(opened, environ=environ)
            return await configure.configure(ctx, host, names, plan)

    return asyncio.run(body())


def _fed(heard: list[jobs.Event]) -> model.Run:
    """The run's events as its live view's model takes them: each accepted, as configure declares its steps."""
    run = model.Run(configure.DECLARED, 0.0)
    for event in heard:
        run.feed(event, 1.0)
    return run


def _ran(machine: Machine) -> list[list[str]]:
    """The words of every connection the stand-in ssh made to machine."""
    return [call["argv"] for call in machine.calls() if call["outcome"] == "ran"]


def _reports(heard: list[jobs.Event]) -> list[str]:
    return [event.text for event in heard if event.kind == "report"]


def _ends(heard: list[jobs.Event]) -> list[tuple[str | None, str, object]]:
    return [(event.machine, event.text, event.data.get("outcome")) for event in heard if event.kind == "end"]


def test_a_vm_left_by_provision_runs_its_three_parts_and_stays_unfinished_naming_layer_2_s(
    made, job, heard, swapped
) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    _set("vm_ssh_options", {"ServerAliveInterval": "7"})
    swapped.machines[machine.identity] = machine
    calls, sent = len(machine.calls()), len(made.sent())
    [done] = _configured(("delta",), configure.Plan.read(), job)
    assert (done.name, done.identity) == ("delta", machine.identity)
    assert (done.outcome, done.stage) == ("succeeded", "unfinished")
    # Each part of layer 1 ran, given as the child's tags, and the variables made for those parts alone.
    for part in system.PARTS:
        assert (machine.home / TOUCHED.format(part=part)).is_file(), part
    [call] = swapped.calls
    assert call["tags"] == system.PARTS and call["play"] == swapped.play and call["host_flag"] == ""
    assert call["target"].identity == machine.identity and call["target"].routed_as == "direct"
    variables = json.loads(call["variables"].read_text())
    assert {"common_user", "vscode_server_user", "dev_tools_user"} <= set(variables)
    # The machine's hostname is the name a person chose, never its identity; every string wrapped, as F36 writes it.
    assert variables["common_hostname"] == {"__ansible_unsafe": "delta"}
    assert not [name for name in variables if name.startswith("ansible_")]
    # Its user's real home and its workspace, read over its connection, for G8's alias.
    assert (done.home, done.workspace) == (str(machine.home), f"{machine.home}/projects")
    # Layer 2's three parts named, the stage left unfinished and its digest empty: nothing written to Proxmox.
    assert any(
        "delta: claude_code, github_projects and tailscale were not run, which card G9 builds" in line
        for line in _reports(heard)
    ), _reports(heard)
    assert made.writes(sent) == [] and made.record(vmid).stage == "unfinished" and made.record(vmid).converged == ""
    # Every connection to it by its identity, its key checked strictly in the product's file, with the person's own
    # options.
    words = [call["argv"] for call in machine.calls()[calls:] if call["outcome"] == "ran"]
    assert words
    for argv in words:
        assert f"HostKeyAlias={machine.identity}" in argv and "StrictHostKeyChecking=yes" in argv, argv
        assert "BatchMode=yes" in argv and "ServerAliveInterval=7" in argv, argv
        assert any(word.startswith("UserKnownHostsFile=") and "agent-warehouse.known_hosts" in word for word in argv)
    assert [argv for argv in words if "getent passwd dev" in " ".join(argv)], words
    # The model takes every event: configure's steps declared, layer 1's as lines of work.
    fed = _fed(heard)
    assert fed.outcomes[machine.identity].outcome == "succeeded"
    assert ("delta" in fed.name(machine.identity)) and _ends(heard) == [
        (machine.identity, "configured, unfinished", "succeeded")
    ]


def test_tags_common_runs_that_part_alone_and_tags_with_no_names_finds_every_vm(made, job, heard, swapped) -> None:
    found = _provisioned(made, "delta", "echo")
    for _, machine in found.values():
        swapped.machines[machine.identity] = machine
    [done] = _configured(("delta",), configure.Plan.read("common"), job)
    delta, echo = found["delta"][1], found["echo"][1]
    assert done.outcome == "succeeded" and not [line for line in _reports(heard) if "card G9" in line]
    assert [part for part in system.PARTS if (delta.home / TOUCHED.format(part=part)).exists()] == ["common"]
    variables = json.loads(swapped.calls[0]["variables"].read_text())
    assert "common_user" in variables and "vscode_server_user" not in variables and "dev_tools_user" not in variables
    assert swapped.calls[0]["tags"] == ("common",)
    # With no names, --tags still finds the machines it is meant to configure: every VM of the pool this fleet owns.
    ended = _configured((), configure.Plan.read("dev_tools"), job)
    assert sorted(each.name for each in ended) == ["delta", "echo"] and all(each.ok for each in ended)
    for machine in (delta, echo):
        assert (machine.home / TOUCHED.format(part="dev_tools")).is_file()
    assert not (echo.home / TOUCHED.format(part="common")).exists()


def test_a_failed_task_fails_the_vm_naming_the_task_and_cloud_init_s_in_this_engine_s_lines(
    made, job, heard, swapped
) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    swapped.machines[machine.identity] = machine
    swapped.write(
        [
            {
                "name": "Check the disk",
                "ansible.builtin.fail": {"msg": "{{ inventory_hostname }}: the disk is full"},
                "tags": ["common"],
            }
        ]
    )
    sent = len(made.sent())
    [done] = _configured(("delta",), configure.Plan.read(), job)
    assert (done.outcome, done.home, done.workspace) == ("failed", "", "")
    [(_, why, outcome)] = _ends(heard)
    # Named by the task, the machine's name in place of its identity, which a person never typed.
    assert outcome == "failed" and why == 'delta: the task "Check the disk" failed: delta: the disk is full', why
    assert machine.identity not in why
    assert _fed(heard).outcomes[machine.identity].outcome == "failed"
    assert made.writes(sent) == []
    # cloud-init's failure, in this engine's lines: no just, no ANSIBLE_ARGS.
    heard.clear()
    swapped.write(
        [{"name": configure.CLOUD_INIT_TASK, "ansible.builtin.fail": {"msg": "just destroy x"}, "tags": ["always"]}]
    )
    [done] = _configured(("delta",), configure.Plan.read(), job)
    [(_, why, outcome)] = _ends(heard)
    assert outcome == "failed" and "cloud-init's first boot ended in an error" in why, why
    assert "aw destroy delta, then aw deploy delta --only provision and aw deploy delta --only configure" in why
    assert "--set configure_wait_for_cloud_init=false" in why and "just" not in why and "ANSIBLE_ARGS" not in why


def test_a_run_whose_events_no_callback_of_the_product_s_wrote_is_refused(made, job, heard, swapped) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    swapped.machines[machine.identity] = machine
    # A callback of the product's name beside the play, loaded in its place (F5's review 2): it writes nothing.
    plugins = swapped.folder / "callback_plugins"
    plugins.mkdir()
    (plugins / f"{layer1.CALLBACK}.py").write_text(
        "from ansible.plugins.callback import CallbackBase\n\n\n"
        "class CallbackModule(CallbackBase):\n"
        "    CALLBACK_VERSION = 2.0\n"
        "    CALLBACK_TYPE = 'stdout'\n"
        f"    CALLBACK_NAME = {layer1.CALLBACK!r}\n"
    )
    [done] = _configured(("delta",), configure.Plan.read(), job)
    [(_, why, outcome)] = _ends(heard)
    assert done.outcome == "failed" and "no event of the product's own callback was heard" in why, why
    assert "aw deploy delta --only configure" in why


def test_every_secret_the_run_holds_is_struck_from_what_layer_1_says(made, job, heard, swapped) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    swapped.machines[machine.identity] = machine
    secret = (made.home.parent / "token-secret").read_text().strip()
    assert secret
    swapped.write([{"name": "Say it", "ansible.builtin.debug": {"msg": f"the token is {secret}"}, "tags": ["common"]}])
    [done] = _configured(("delta",), configure.Plan.read("common"), job)
    assert done.outcome == "succeeded"
    said = [event for event in heard if event.kind == "step" and event.data.get("task") == "Say it"]
    assert said and all(secret not in json.dumps(event.data) + event.text for event in said), said
    assert any("the token is" in str(event.data.get("message", "")) for event in said)
    assert all(secret not in json.dumps(event.data) + event.text for event in heard)
    # Nor on any connection's command line, nor in the play's variables.
    assert all(secret not in " ".join(call["argv"]) for call in machine.calls())
    assert secret not in swapped.calls[0]["variables"].read_text()
    assert [str(each) for each in swapped.calls[0]["redacted"]] and len(swapped.calls[0]["redacted"]) >= 1


def test_a_stopped_vm_is_started_first_where_the_plan_says(made, job, heard, swapped) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    swapped.machines[machine.identity] = machine
    _pve(made.base, "POST", f"nodes/pve/qemu/{vmid}/status/stop")
    [done] = _configured(("delta",), configure.Plan.read(), job)
    assert done.outcome == "left stopped" and made.status(vmid) == "stopped" and swapped.calls == []
    assert _fed(heard).outcomes[machine.identity].outcome == "succeeded"
    sent = len(made.sent())
    [done] = _configured(("delta",), configure.Plan.read(None, True), job)
    assert done.outcome == "succeeded" and made.status(vmid) == "running"
    assert [entry["path"].rsplit("/", 2)[-2:] for entry in made.writes(sent)] == [["status", "start"]]


# ---------------------------------------------------------------- through the hop, over the real connection ----


@pytest.fixture
def short() -> Iterator[Path]:
    """The person's home as the route reads it: short enough for the sockets' paths in its .ansible/cp."""
    directory = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    yield Path(directory)
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def hopped(monkeypatch, tmp_path, installed, made, engine_guard) -> Swapped:
    """The play swapped, layer 1 over its own SSH connection; the stand-in Proxmox host the hop, at the fake's own
    address, where host.yml points the host."""
    if not Path(layer1.PYTHON).is_file():
        pytest.skip(f"this machine has no {layer1.PYTHON}, the machine's interpreter the inventory names")
    made.machines.append(hop(made.root, address=LOOPBACK))
    engine_guard.first_on_path(made.root / SSH_FOLDER)
    return Swapped(monkeypatch, tmp_path / "play", False)


def test_a_configure_through_a_stand_in_hop_reaches_the_vm_only_through_it_and_a_refused_hop_stops_before_it(
    made, job, heard, hopped, short
) -> None:
    [(vmid, machine)] = _provisioned(made, "delta").values()
    pve = made.machines[0]
    assert pve.address == LOOPBACK
    _set(ROUTE, "host")
    calls = len(machine.calls())
    hop_modes(pve, login="refused")
    with pytest.raises(routes.Unusable, match="ssh could not log in there"):
        _configured(("delta",), configure.Plan.read("common"), job, short)
    assert len(machine.calls()) == calls and hopped.calls == []
    hop_modes(pve, login="open")
    [done] = _configured(("delta",), configure.Plan.read("common"), job, short)
    assert done.outcome == "succeeded", _ends(heard)
    assert (machine.home / TOUCHED.format(part="common")).is_file()
    # The frame's connections and layer 1's own all rode the hop to the VM's address; nothing dialled it from here.
    words = [call["argv"] for call in machine.calls()[calls:] if call["outcome"] == "ran"]
    assert len(words) >= 3, words
    for argv in words:
        assert any(word.startswith("ProxyCommand=") for word in argv), argv
        assert f"HostKeyAlias={machine.identity}" in argv and "StrictHostKeyChecking=yes" in argv, argv
    assert f"forward {machine.address}:22" in [call["outcome"] for call in pve.calls()]
    # Layer 1's inventory names the VM's Python and sudo by their paths, and the connection the route gave it.
    [call] = hopped.calls
    assert call["target"].routed_as == "hop"
    inventory = json.loads(rundir.path_for(job, f"inventory-{machine.identity}.yml").read_text())
    [host] = inventory["all"]["hosts"].values()
    assert (host["ansible_python_interpreter"], host["ansible_become_exe"]) == (layer1.PYTHON, layer1.SUDO)


def test_deploy_runs_configure_for_tags_beside_names_and_reaches_every_vm_with_none() -> None:
    assert deploy.stage_of({"names": ["delta"], "tags": "common"}) == "configure"
    assert deploy.stage_of({"tags": "common"}) is None and deploy.stage_of({"names": ["delta"]}) is None
    assert deploy.stage_of({"only": "configure"}) == "configure"
    assert deploy.stage_of({"only": "provision", "names": ["delta"], "tags": "common"}) == "provision"
