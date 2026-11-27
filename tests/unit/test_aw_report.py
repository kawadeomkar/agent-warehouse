"""What a run says of a machine, and its NEXT STEPS record straight after: lib/claude_vm/engine/report.py's `say`
(card G6; docs/architecture/engine.md §16.4), which carries for the new engine what
tests/unit/test_attention_callback.py holds for today's callback (that file unchanged).

Without the fake: `say` emits the report, then the `next` record of its machine and matter, the line or "" once nothing
is left; it refuses a matter MATTERS has not, a line of more than one line and a form; the engine's matters sit at their
named places of run/next_steps.py's MATTERS, today's in today's order, so that today's records give today's lines; the
run's model folds what `say` emitted in that order; status/lines.py's words name the program by its parameter, just by
default, and on aw's road a VM's command is the caller's whole line, with its host. A syntax-tree scan of
lib/claude_vm/engine/ and lib/claude_vm/guest/ fails on a `report` or `next` event emitted any other way than `say`
(each road it refuses planted once).

Against the fake and F30's stand-in machines, each VM made first by G3's `aw deploy --only provision`: a configure of
two prints a summary line for each and the NEXT STEPS block in today's order, through `aw deploy` as a person runs it;
with two hosts, each line that names a VM ends with its host, configure's and a provision's of a VM left stopped (the
case of today's test_every_line_that_names_a_vm_names_its_host, carried; tests/unit/test_hosts.py unchanged); and in
this process, one VM reached through layer 1 and one left stopped, each report followed by its record.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

import pytest
from aw_machine import SSH_FOLDER
from fleet_fixture import with_port
from harness import REPO, _pve
from pki import proxmox_pki
from test_aw_configure import Swapped, _aw, _configured, _fed, _provisioned
from test_aw_create import Made, _making, _ok

from claude_vm.engine import configure, report
from claude_vm.fleet import paths
from claude_vm.run import next_steps as todays
from claude_vm.runs import job as jobs
from claude_vm.runs import model, rundir
from claude_vm.runs.stages import DEPLOY
from claude_vm.runs.view import View
from claude_vm.schema import printer
from claude_vm.status import lines

LIB = REPO / "lib" / "claude_vm"
# The folders whose every report is said through `say`, and the one module that emits them.
SCANNED = (LIB / "engine", LIB / "guest")
SAYER = LIB / "engine" / "report.py"
# The kinds of event only `say` emits.
SAID = ("report", "next")
# Today's matters, in today's order, and the engine's own, each after the matter named beside it.
TODAYS = (
    "init_settings",
    "init_check",
    "vm",
    "claude",
    "remote_control",
    "github",
    "github_identity",
    "tailscale",
    "tailscale_record",
    "editor",
    "destroy",
)
ENGINES = {"fleet": "init_check", "route": "fleet", "template": "route", "configure": "vm"}
ALPHA = "agent-warehouse-4f1c0000-0000-4000-8000-000000000001"
BETA = "agent-warehouse-4f1c0000-0000-4000-8000-000000000002"


class Heard:
    """A run that keeps what it is given, as a job's listener hears it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str | None, str, dict]] = []

    def emit(self, kind: str, machine: str | None, text: str, **data: object) -> None:
        self.events.append((kind, machine, text, data))


# ---------------------------------------------------------------- without the fake ----


def test_say_emits_the_report_then_its_record_and_an_empty_line_once_nothing_is_left() -> None:
    run = Heard()
    report.say(run, ALPHA, "vm", "alpha is stopped", "aw deploy alpha --only provision --start")
    report.say(run, ALPHA, "vm", "alpha is up at 192.0.2.7", "")
    report.say(run, None, "template", "The base template was built.", "")
    assert run.events == [
        ("report", ALPHA, "alpha is stopped", {}),
        ("next", ALPHA, "aw deploy alpha --only provision --start", {"matter": "vm"}),
        ("report", ALPHA, "alpha is up at 192.0.2.7", {}),
        ("next", ALPHA, "", {"matter": "vm"}),
        ("report", None, "The base template was built.", {}),
        ("next", None, "", {"matter": "template"}),
    ]


@pytest.mark.parametrize(
    ("matter", "text", "line", "error"),
    [
        ("something_new", "said", "", ValueError),
        ("", "said", "", ValueError),
        ("vm", "said", None, TypeError),
        ("vm", None, "", TypeError),
        ("vm", "said", "aw deploy alpha\naw deploy beta", ValueError),
        ("vm", "said", "aw deploy alpha\n", ValueError),
        ("vm", "said", "aw destroy <name>... --confirm <names>", ValueError),
        ("vm", "said", "aw deploy alpha --cores <cores>", ValueError),
        ("vm", "said", "aw deploy alpha --set <name>=<value>", ValueError),
    ],
)
def test_say_refuses_before_anything_is_emitted(matter, text, line, error) -> None:
    run = Heard()
    with pytest.raises(error):
        report.say(run, ALPHA, matter, text, line)
    assert run.events == []


def test_the_engine_s_matters_are_at_their_named_places_and_today_s_keep_their_order() -> None:
    matters = todays.MATTERS
    assert tuple(matter for matter in matters if matter not in ENGINES) == TODAYS
    for matter, after in ENGINES.items():
        assert matters.index(matter) == matters.index(after) + 1, matter
    # Today's records give today's lines, in today's order: no report of today's records an engine's matter.
    records = {
        "claude_vm_next": {"b": {"destroy": "b left"}, "a": {"vm": "a stopped"}, "just init": {"init_check": "c"}},
        "tailscale_next": {"a": {"tailscale": "a ts"}},
        "claude_code_next": {"a": {"remote_control": "a rc", "claude": "a claude"}},
        "code_next": {"a": {"something_new": "new", "editor": "open"}},
    }
    assert todays.next_steps(records) == ["a stopped", "a claude", "a rc", "a ts", "open", "new", "b left", "c"]


def test_the_model_folds_what_say_emitted_by_machine_then_matter_the_run_s_own_first() -> None:
    where = paths.jobs_dir(paths.home(REPO))
    made = jobs.create(where, "deploy", "deploy", "pve", [ALPHA, BETA], {"kind": "terminal"}, True)
    try:
        run = model.Run(DEPLOY, 0.0, {ALPHA: "alpha", BETA: "beta"})
        made.listen(lambda event: run.feed(event, 1.0))
        report.say(made, BETA, "vm", "beta is stopped", "aw deploy beta --only provision --start")
        report.say(made, ALPHA, "configure", "alpha: claude_code was not run", "")
        report.say(made, ALPHA, "claude", "alpha is not signed in", "aw login alpha --only claude")
        report.say(made, ALPHA, "vm", "alpha is stopped", "aw deploy alpha --only provision --start")
        report.say(made, None, "template", "The base template is rebuilt.", "aw deploy --only template")
        report.say(made, None, "fleet", "Left out: VM 9013.", "")
        assert run.next_steps() == [
            "aw deploy --only template",
            "aw deploy alpha --only provision --start",
            "aw login alpha --only claude",
            "aw deploy beta --only provision --start",
        ]
        # Taken back by the next report of the same machine and matter, in the run's record as in the model.
        report.say(made, ALPHA, "vm", "alpha is up at 192.0.2.7", "")
        assert "aw deploy alpha --only provision --start" not in run.next_steps()
        assert [step["line"] for step in made.read()["next"] if step["machine"] == ALPHA] == [
            "aw login alpha --only claude"
        ]
    finally:
        jobs.drop(made)


def _tailscale(**given: object) -> dict:
    """A VM's Tailscale facts as found.py gives them, installed and not signed in, with given in place."""
    facts = {
        "name": "alpha",
        "hostname": "alpha",
        "check_mode": False,
        "ours": True,
        "check_without_package": False,
        "daemon_up": True,
        "backend": "NeedsLogin",
        "joined": False,
        "auth_url": "",
        "first_join": False,
        "key_wanted": False,
        "key_refused_locally": False,
        "key_held_back": False,
        "join_ran": False,
        "join_stderr": "",
        "refused_why": "",
        "dns_name": "",
        "self_name": "",
        "tailnet": "",
        "login_server": "",
        "ipv4": "",
        "joined_now": False,
        "login_ours": False,
        "name_taken": False,
        "set_changed": False,
        "set_cmd": [],
        "from_login": False,
        "set_flags": [],
        "tags_missing": [],
        "settle_seconds": 0,
        "expired_elsewhere": False,
        "joined_tag": "",
        "joined_before": False,
        "sign_in_follows": False,
        "auth_method": "auto",
        "admin_page": "https://login.tailscale.com/admin/machines",
        "configure_command": "CONFIGURE",
        "login_command": "LOGIN",
    }
    return {**facts, **given}


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"sign_in_follows": True}, "{program} deploy signs it in next, in this run."),
        ({}, "joins it on the next {program} deploy --only configure."),
    ],
)
def test_status_lines_name_the_program_by_its_parameter_just_by_default(given, said) -> None:
    facts = _tailscale(**given)
    assert said.format(program="just") in lines.tailscale_report(facts)["line"]
    assert said.format(program="aw") in lines.tailscale_report(facts, program="aw")["line"]
    assert "just" not in lines.tailscale_report(facts, program="aw")["line"]


@pytest.mark.parametrize("backend", [{"check_without_package": True}, {"backend": "Running", "daemon_up": False}])
def test_a_line_status_lines_names_a_vm_by_is_the_caller_s_whole_line_on_aw_s_road(backend) -> None:
    # Today's just writes check mode's line itself; on aw's road the caller gives the printer's line, with its host.
    facts = _tailscale(check_mode=True, **backend)
    assert "just deploy alpha --only configure" in lines.tailscale_report(facts)["line"]
    with pytest.raises(ValueError, match="reconfigure_command"):
        lines.tailscale_report(facts, program="aw")
    again = printer.line("deploy", {"names": ["alpha"], "only": "configure"}, refused=False, host_flag="pve")
    said = lines.tailscale_report({**facts, "reconfigure_command": again}, program="aw")["line"]
    assert "aw deploy alpha --only configure --host pve" in said and "just" not in said, said


# ---------------------------------------------------------------- the scan ----


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of every docstring's node: a module's, a class's, a function's."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _kind(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    return next((keyword.value for keyword in call.keywords if keyword.arg == "kind"), None)


def _emitted_otherwise(source: str) -> list[int]:
    """The lines of source that emit a report or a NEXT STEPS record any other way than `say`: an emit whose kind is
    one of SAID, or is worked out rather than written (a wrapper named emit may hand on its own parameter), or a string
    of SAID anywhere but a docstring, so that a kind kept in a name, a partial or a mapping is found too."""
    tree = ast.parse(source)
    docstrings = _docstrings(tree)
    found = []

    def walk(node: ast.AST, wrapper: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
            wrapper = names if node.name == "emit" else set()
        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if called == "emit":
                kind = _kind(node)
                written = isinstance(kind, ast.Constant) and isinstance(kind.value, str) and kind.value not in SAID
                handed_on = isinstance(kind, ast.Name) and kind.id in wrapper
                if not (written or handed_on):
                    found.append(node.lineno)
        if isinstance(node, ast.Constant) and node.value in SAID and id(node) not in docstrings:
            found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            walk(child, wrapper)

    walk(tree, set())
    return sorted(set(found))


def test_no_report_and_no_next_steps_record_is_emitted_but_by_say() -> None:
    files = sorted(path for folder in SCANNED for path in folder.rglob("*.py"))
    assert SAYER in files and len(files) > 10
    found = {str(path.relative_to(REPO)): _emitted_otherwise(path.read_text()) for path in files if path != SAYER}
    assert {path: at for path, at in found.items() if at} == {}
    # The one module that emits them, each once, the report first.
    assert _emitted_otherwise(SAYER.read_text()) != []
    calls = [node for node in ast.walk(ast.parse(SAYER.read_text())) if isinstance(node, ast.Call)]
    kinds = [_kind(call).value for call in calls if getattr(call.func, "attr", "") == "emit"]
    assert kinds == ["report", "next"]


@pytest.mark.parametrize(
    "source",
    [
        'ctx.emit("report", None, "said")',
        'self.emit("report", "said")',
        'job.emit("next", machine, "aw status", matter="vm")',
        'emit(kind="report", machine=None, text="said")',
        'KIND = "report"\nctx.emit(KIND, None, "said")',
        'functools.partial(ctx.emit, "report")(None, "said")',
        'ctx.emit(*("report", None, "said"))',
        'ctx.emit("re" + "port", None, "said")',
        "def emit(self, text):\n    self.ctx.emit(self.kind, self.key, text)",
        "def say(self, kind, text):\n    self.ctx.emit(kind, self.key, text)",
    ],
)
def test_the_scan_finds_each_other_road(source) -> None:
    assert _emitted_otherwise(source) != [], source


@pytest.mark.parametrize(
    "source",
    [
        'ctx.emit("step", None, "address", stage="provision")',
        "def emit(self, kind, text, **data):\n    self.ctx.emit(kind, self.key, text, **data)",
        'def f():\n    """It says a "report" and a "next" record."""\n    report.say(ctx, None, "vm", "said", "")',
    ],
)
def test_the_scan_lets_say_s_way_and_other_events_stand(source) -> None:
    assert _emitted_otherwise(source) == [], source


# ---------------------------------------------------------------- against the fake ----


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made(home, tmp_path, pki) -> Iterator[Made]:
    """A whole fleet against the fake, as test_aw_create.py's."""
    yield from _making(home, tmp_path, pki, True)


def _stopped(made: Made, *names: str) -> dict[str, int]:
    """names made by G3's provision at stand-in machines, then each stopped in Proxmox: by name, its VMID."""
    found = {name: vmid for name, (vmid, _) in _provisioned(made, *names).items()}
    for vmid in found.values():
        _pve(made.base, "POST", f"nodes/pve/qemu/{vmid}/status/stop")
    return found


def _block(stdout: str) -> list[str]:
    """The lines under the NEXT STEPS heading the run printed last, as text.banner draws it."""
    printed = stdout.split("\n")
    at = printed.index("##  NEXT STEPS")
    assert set(printed[at - 1]) == set(printed[at + 1]) == {"#"} and printed.count("##  NEXT STEPS") == 1, stdout
    return [line for line in printed[at + 2 :] if line.strip(" \a")]


def _summary_line(stdout: str, name: str) -> str:
    """The summary's line under the heading of a machine, its name in capitals."""
    printed = stdout.split("\n")
    at = printed.index(name.upper())
    assert at < printed.index("##  NEXT STEPS"), stdout
    return printed[at + 1].strip()


def test_a_configure_of_two_machines_prints_a_summary_line_each_and_next_steps_in_today_s_order(made) -> None:
    vmids = _stopped(made, "echo", "delta")
    ran = _aw(made, "--only", "configure")
    _ok(ran)
    for name in ("delta", "echo"):
        said = f'VM "{name}" (VMID {vmids[name]}) is stopped, so it was left as it is and not configured.'
        assert _summary_line(ran.stdout, name) == said, ran.stdout
    assert _block(ran.stdout) == [
        "aw deploy delta --only provision --start",
        "aw deploy echo --only provision --start",
    ], ran.stdout


def test_with_two_hosts_every_line_that_names_a_vm_names_its_host(made) -> None:
    _stopped(made, "delta", "echo")
    # A second host of the fleet, set up as a person begins one: the fleet has two from here on.
    with_port(int(made.base.rsplit(":", 1)[1]), "nuc")
    ran = _aw(made, "delta", "echo", "--only", "configure")
    _ok(ran)
    assert "delta --only provision --start --host pve" in " ".join(ran.stdout.split()), ran.stdout
    assert _block(ran.stdout) == [
        "aw deploy delta --only provision --start --host pve",
        "aw deploy echo --only provision --start --host pve",
    ], ran.stdout


def test_a_provision_that_leaves_a_vm_stopped_records_its_line_with_its_host_where_there_are_two(made) -> None:
    _stopped(made, "delta")
    ran = made.deploy("delta")
    _ok(ran)
    assert "already exists and is stopped" in ran.stdout, ran.stdout
    assert _block(ran.stdout) == ["aw deploy delta --only provision --start"], ran.stdout
    # A second host of the fleet, set up as a person begins one: the fleet has two from here on.
    with_port(int(made.base.rsplit(":", 1)[1]), "nuc")
    ran = made.deploy("delta")
    _ok(ran)
    assert _block(ran.stdout) == ["aw deploy delta --only provision --start --host pve"], ran.stdout


@pytest.fixture
def job(made) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that configures has; gone after."""
    made_job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy delta echo", "pve", [], {"kind": "terminal"}, True
    )
    rundir.make(made_job)
    yield made_job
    rundir.remove(made_job)
    jobs.drop(made_job)


def test_in_process_each_report_is_followed_by_its_record_and_the_view_says_each_machine_once(
    made, job, monkeypatch, tmp_path, engine_guard
) -> None:
    engine_guard.first_on_path(made.root / SSH_FOLDER)
    swapped = Swapped(monkeypatch, tmp_path / "play", True)
    found = _provisioned(made, "delta", "echo")
    (delta_vmid, delta), (echo_vmid, _) = found["delta"], found["echo"]
    _pve(made.base, "POST", f"nodes/pve/qemu/{echo_vmid}/status/stop")
    swapped.machines[delta.identity] = delta
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    done = {each.name: each for each in _configured(("delta", "echo"), configure.Plan.read(), job)}
    assert (done["delta"].outcome, done["echo"].outcome) == ("succeeded", "left stopped")
    # Each machine's reports and records alternate, each report followed by its record of the same machine.
    for machine in {event.machine for event in heard if event.kind in SAID}:
        kinds = [event.kind for event in heard if event.machine == machine and event.kind in SAID]
        assert kinds == ["report", "next"] * (len(kinds) // 2), (machine, kinds)
    fed = _fed(heard)
    assert fed.next_steps() == ["aw deploy echo --only provision --start"]
    summary = View(fed, started_at=0.0).summary(2.0, 120, False)
    assert summary.count("DELTA") == 1 and summary.count("ECHO") == 1, summary
    delta_said = summary[summary.index("DELTA") + 1].strip()
    echo_said = summary[summary.index("ECHO") + 1].strip()
    assert delta_said.startswith("claude_code, github_projects and tailscale were not run"), summary
    assert echo_said == f'VM "echo" (VMID {echo_vmid}) is stopped, so it was left as it is and not configured.'
    assert delta_vmid != echo_vmid
