"""The CI workflow's gate, and the releases: each workflow's own script run on what GitHub would hand it.

`CI result`, the one check branch protection requires, holds every other job to it. Required alone, it fails open
where a list of required job names failed closed: a job added to the workflow and left out of its `needs`, or one
allowed to fail, could fail and the pull request still merge. So the job set is held here, and so is the Molecule
image every scenario runs, pinned by digest, which the weekly canary runs unpinned, and the wait for its containers'
boot that every scenario starts with, since the boot empties /tmp under a module's payload.

A release is an annotated SemVer tag on main (release.yml), its notes the pull requests merged since the last one,
grouped by the label labels.yml gives each from its title's tag (.github/release.yml). Their scripts run here
against a stand-in gh, which records what it was asked, and a scratch repository, never GitHub. What a version
number promises about, as CONTRIBUTING.md lists it, is held to the justfile.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import tomllib
import yaml
from harness import GIT_ENV
from molecule.constants import DEFAULT_CONFIG

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
JOBS = WORKFLOW["jobs"]
RESULT = JOBS["result"]
# Not part of the gate: it runs on the weekly schedule against the unpinned image and may fail on purpose.
OUTSIDE = {"canary"}
NEEDS_JQ = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed (GitHub's runners have it)")


def test_ci_result_needs_every_job_but_the_canary():
    gate = set(JOBS) - {"result"} - OUTSIDE
    assert RESULT["name"] == "CI result"
    assert set(RESULT["needs"]) == gate, (sorted(RESULT["needs"]), sorted(gate))


def test_ci_result_runs_whatever_its_jobs_did():
    # Without always() a failed job skips it, and a skipped required check passes. The area stack's gate alone skips
    # it, on a pull request of the stack, where no job runs (the tests below).
    assert RESULT["if"] == f"always() && {STACK_GATE}"


def test_no_job_of_the_gate_can_fail_and_still_succeed():
    # continue-on-error makes a failure a success in `needs`, whether set on the job or one of its steps.
    for name in set(JOBS) - OUTSIDE:
        job = JOBS[name]
        assert "continue-on-error" not in job, name
        for step in job.get("steps", []):
            assert "continue-on-error" not in step, (name, step.get("name"))


@NEEDS_JQ
@pytest.mark.parametrize(
    ("results", "passes"),
    [
        ({}, True),
        ({"dependency-review": "skipped"}, True),
        ({"molecule": "failure"}, False),
        ({"playbooks": "cancelled"}, False),
        ({"lint": "skipped", "molecule": "failure"}, False),
    ],
)
def test_ci_result_passes_only_when_each_job_succeeded_or_was_skipped(tmp_path, results, passes):
    (step,) = RESULT["steps"]
    needs = {name: {"result": results.get(name, "success"), "outputs": {}} for name in RESULT["needs"]}
    run, _ = _run(step["run"], tmp_path, tmp_path, NEEDS=json.dumps(needs))
    assert (run.returncode == 0) is passes, (results, run.stdout, run.stderr)
    for name in RESULT["needs"]:
        assert f"{name}: {results.get(name, 'success')}" in run.stdout


# ---------------------------------------------------------------------------------- the area stack's pull requests ---

# The gate every job runs under: a pull request whose head branch starts with stack/ (the area stack's, each a part of
# the tree the pull request from base-first carries whole, whose run is a batch's whole run) runs no job, CI result
# included. github.head_ref is a pull request's head branch and empty for every other event.
STACK_GATE = "!startsWith(github.head_ref, 'stack/')"
# A job's own condition, where it has one, which the gate is joined after.
OWN_CONDITION = {
    "dependency-review": "github.event_name == 'pull_request'",
    "canary": "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')",
    "result": "always()",
}
# Each kind of run that starts ci.yml, by the contexts GitHub hands its jobs' conditions.
STACK_PULL = {
    "github.event_name": "pull_request",
    "github.head_ref": "stack/aw-11-batch-1",
    "github.base_ref": "stack/aw-10-ci",
    "github.ref": "refs/pull/322/merge",
}
BASE_FIRST_PULL = {
    "github.event_name": "pull_request",
    "github.head_ref": "base-first",
    "github.base_ref": "main",
    "github.ref": "refs/pull/310/merge",
}
# A branch that names the stack but is none of its: the gate is a prefix of the head branch, not a word in it.
OTHER_PULL = BASE_FIRST_PULL | {"github.head_ref": "ci/a35-stack-gate", "github.base_ref": "base-first"}
PUSH = {"github.event_name": "push", "github.head_ref": "", "github.base_ref": "", "github.ref": "refs/heads/main"}
SCHEDULE = PUSH | {"github.event_name": "schedule"}
DISPATCH = PUSH | {"github.event_name": "workflow_dispatch"}

# The words of GitHub's expressions that ci.yml's conditions use; any other character is a token of its own, refused.
EXPRESSION_TOKENS = re.compile(
    r"\s*(?:(?P<string>'[^']*')|(?P<op>&&|\|\||==|!=|[!(),])|(?P<name>[A-Za-z_][\w.-]*)|(?P<other>\S))"
)
EXPRESSION_FUNCTIONS = {
    # GitHub compares strings ignoring case, startsWith's included.
    "startsWith": lambda text, prefix: str(text).casefold().startswith(str(prefix).casefold()),
    # The status functions, read for a run in which every job a job needs passed.
    "always": lambda: True,
    "success": lambda: True,
}


def _runs(condition: str, context: dict[str, str]) -> bool:
    """Whether a job whose if: is condition runs, for a run GitHub hands context: its expressions as far as ci.yml's
    conditions use them (a string with no quote in it, a context, a call of EXPRESSION_FUNCTIONS, !, == and !=
    between strings, && and ||, parentheses), each operator at GitHub's precedence. Anything else raises, so that a
    condition this cannot read fails the test it is read in, rather than being taken for true or for false."""
    wrapped = re.fullmatch(r"\s*\$\{\{(.*)\}\}\s*", condition, re.S)
    words = EXPRESSION_TOKENS.finditer(wrapped[1] if wrapped else condition)
    tokens = [(each.lastgroup, each[each.lastgroup]) for each in words]
    at = 0

    def peek() -> str | None:
        return tokens[at][1] if at < len(tokens) else None

    def take(expected: str | None = None) -> tuple[str, str]:
        nonlocal at
        if at == len(tokens) or expected not in (None, tokens[at][1]):
            raise ValueError(f"{condition!r}: {expected or 'a word'} expected at its token {at}")
        at += 1
        return tokens[at - 1]

    def either():
        value = both()
        while peek() == "||":
            take()
            right = both()
            value = value or right
        return value

    def both():
        value = compared()
        while peek() == "&&":
            take()
            right = compared()
            value = value and right
        return value

    def compared():
        left = negated()
        if peek() not in ("==", "!="):
            return left
        _, operator = take()
        right = negated()
        if not (isinstance(left, str) and isinstance(right, str)):
            raise ValueError(f"{condition!r}: only strings are compared here")
        return (left.casefold() == right.casefold()) is (operator == "==")

    def negated():
        if peek() == "!":
            take()
            return not negated()
        return operand()

    def operand():
        kind, word = take()
        if word == "(":
            value = either()
            take(")")
            return value
        if kind == "string":
            return word[1:-1]
        if kind == "name" and peek() == "(":
            take("(")
            arguments = []
            while peek() != ")":
                if arguments:
                    take(",")
                arguments.append(either())
            take(")")
            return EXPRESSION_FUNCTIONS[word](*arguments)
        if kind == "name":
            return context[word]
        raise ValueError(f"{condition!r}: {word!r} where a value goes")

    value = either()
    if at != len(tokens):
        raise ValueError(f"{condition!r}: words left after its token {at}")
    return bool(value)


def test_every_job_runs_under_the_area_stack_s_gate_joined_after_its_own_condition():
    # Every job, read from the workflow, the canary too, which runs on no pull request, so that one rule reads every
    # job: a job added later without the gate fails here, as does a gate written another way.
    for name, job in JOBS.items():
        own = OWN_CONDITION.get(name)
        assert job.get("if") == (f"{own} && {STACK_GATE}" if own else STACK_GATE), (name, job.get("if"))
    assert set(OWN_CONDITION) <= set(JOBS), sorted(OWN_CONDITION)


@pytest.mark.parametrize(
    ("context", "running"),
    [
        (STACK_PULL, set()),
        (BASE_FIRST_PULL, set(JOBS) - OUTSIDE),
        (OTHER_PULL, set(JOBS) - OUTSIDE),
        (PUSH, set(JOBS) - OUTSIDE - {"dependency-review"}),
        (SCHEDULE, set(JOBS) - {"dependency-review"}),
        (DISPATCH, set(JOBS) - {"dependency-review"}),
    ],
    ids=["a pull request of the stack", "base-first's", "another branch's", "a push", "the schedule", "a manual run"],
)
def test_a_pull_request_of_the_area_stack_runs_no_job_and_every_other_run_the_jobs_it_ran(context, running):
    # Each job's if: read as GitHub reads it, a job with none run as GitHub runs it: on a pull request of the stack no
    # job takes a runner, CI result included; on base-first's every job of the gate, dependency review included; on a
    # push the same but dependency review; on the schedule and a manual run those and the canary.
    ran = {name for name, job in JOBS.items() if _runs(job.get("if", "success()"), context)}
    assert ran == running, (sorted(ran - running), sorted(running - ran))


@pytest.mark.parametrize(
    ("condition", "runs"),
    [
        ("!startsWith(github.head_ref, 'stack/')", False),
        ("${{ !startsWith(github.head_ref, 'Stack/') }}", False),
        ("!startsWith(github.head_ref, 'stack/aw-12')", True),
        ("!(github.event_name == 'pull_request') || github.head_ref != 'stack/aw-11-batch-1'", False),
        ("github.event_name == 'pull_request' || github.event_name == 'push' && github.head_ref == ''", True),
        ("!github.base_ref", False),
        ("'Base-First' == 'base-first'", True),
    ],
)
def test_the_reading_of_a_condition_is_github_s(condition, runs):
    # The reader the test above holds the jobs with, on a pull request of the stack: GitHub's precedence (&& before
    # ||), its strings compared ignoring case, a string that is not empty true.
    assert _runs(condition, STACK_PULL) is runs


@pytest.mark.parametrize(
    "condition",
    [
        "contains(github.head_ref, 'stack/')",
        "github.event.pull_request.head.ref == 'x'",
        "always() &&",
        "always() always()",
        "github.head_ref == 1",
        "(always()",
        "always() & true",
        "!github.base_ref == 'x'",
        "github.head_ref == 'it''s'",
    ],
)
def test_a_condition_the_reading_does_not_know_fails_it(condition):
    # A function, a context or a form it was not taught is refused, never taken for true or false.
    with pytest.raises((ValueError, KeyError)):
        _runs(condition, STACK_PULL)


# ------------------------------------------------------------------------------------------ the Molecule image ---

IMAGE = re.compile(r"\$\{MOLECULE_IMAGE:-(?P<repository>[\w./-]+)@sha256:[0-9a-f]{64}\}")
# Each scenario's folder, the playbook scenarios' and the roles' own.
SCENARIOS = [
    path.parent
    for path in [*sorted(REPO.glob("molecule/*/molecule.yml")), *sorted(REPO.glob("roles/*/molecule/*/molecule.yml"))]
]
SHARED_MOLECULE = yaml.safe_load((REPO / ".config" / "molecule" / "config.yml").read_text())


def _molecule_images() -> dict[str, list[str]]:
    """The image of each platform, by scenario. The shared configuration names none: Molecule replaces a list."""
    return {
        str(path.relative_to(REPO)): [platform["image"] for platform in yaml.safe_load(path.read_text())["platforms"]]
        for path in (scenario / "molecule.yml" for scenario in SCENARIOS)
    }


def test_every_scenario_runs_the_one_image_pinned_by_digest():
    # Each scenario names its own platforms, so each repeats the image: a bump that missed one would leave that
    # scenario on the old image without a word. A platform in the shared configuration would never be used.
    assert "platforms" not in SHARED_MOLECULE
    images = _molecule_images()
    found = {image for each in images.values() for image in each}
    assert len(found) == 1, images
    (image,) = found
    assert IMAGE.fullmatch(image), image


def test_every_molecule_scenario_the_justfile_orders_is_a_job_of_ci_s():
    # The justfile's molecule_order names every scenario (test_justfile.py holds that), and CI runs each: a role's in
    # the molecule job's matrix, by --role, and the playbook scenarios in the playbooks job's, by the part of just test
    # that runs each - the configure and image scenarios integration's, the new engine's system play its own.
    order = re.search(r"^molecule_order := '([^']*)'$", (REPO / "justfile").read_text(), re.M)[1].split()
    roles = JOBS["molecule"]["strategy"]["matrix"]["role"]
    runs = [step["run"] for step in JOBS["molecule"]["steps"] if "run" in step]
    assert runs == ["just test --role ${{ matrix.role }}"]
    parts = {"configure": "integration", "image": "integration", "provision": "provision", "system": "system"}
    assert sorted(order) == sorted([*roles, *parts])
    assert {entry["type"] for entry in JOBS["playbooks"]["strategy"]["matrix"]["include"]} == set(parts.values())
    runs = [step["run"] for step in JOBS["playbooks"]["steps"] if "run" in step]
    assert runs == ["just test ${{ matrix.type }}"]


def test_the_debian_job_runs_the_vpn_and_editor_roles_on_debian_pinned_by_digest():
    debian = JOBS["debian"]
    assert re.fullmatch(r"geerlingguy/docker-debian12-ansible@sha256:[0-9a-f]{64}", debian["env"]["MOLECULE_IMAGE"])
    assert debian["strategy"]["matrix"]["role"] == ["tailscale", "vscode_server"]
    assert [step["run"] for step in debian["steps"] if "run" in step] == ["just test --role ${{ matrix.role }}"]


def test_one_unit_shard_runs_on_the_oldest_python_the_project_takes_and_every_other_job_on_the_actions():
    # The setup action's default is the checkout's own environment's Python, the newest (test_justfile.py holds it
    # to the justfile's managed_python); one shard of the unit suite runs again on requires-python's floor, so that
    # what only a newer Python has fails there.
    requires = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["requires-python"]
    floor = re.fullmatch(r">=([0-9]+\.[0-9]+)", requires)
    assert floor, requires
    oldest = JOBS["unit-oldest"]
    assert oldest["name"] == f"Unit tests on Python {floor[1]} (1/6)"
    setups = [step for step in oldest["steps"] if step.get("uses") == "./.github/actions/setup"]
    assert [step.get("with") for step in setups] == [{"python-version": floor[1]}]
    shards = len(JOBS["unit"]["strategy"]["matrix"]["shard"])
    assert [step["run"] for step in oldest["steps"] if "run" in step] == [f"just test unit --shard 1/{shards}"]
    # The engine job's matrix names the floor and the default both, which the test below holds it to.
    named = {("ci.yml", "unit-oldest"), ("ci.yml", "engine")}
    others = []
    for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        for name, job in yaml.safe_load(workflow.read_text())["jobs"].items():
            for step in job.get("steps", []):
                if step.get("uses") == "./.github/actions/setup" and (workflow.name, name) not in named:
                    others.append((workflow.name, name, (step.get("with") or {}).get("python-version")))
    assert others and all(version is None for _, _, version in others), others


# A hosted macOS runner named by its version alone: macos-26, never macos-latest or a larger runner's name.
MACOS_BY_VERSION = re.compile(r"macos-[0-9]+\Z")


def test_the_engine_job_runs_on_linux_and_on_macos_pinned_by_its_version_on_the_oldest_python_and_the_actions():
    # The new engine's suite on both systems it runs on and both ends of the Pythons it takes: requires-python's floor
    # and the setup action's default (test_justfile.py holds that to the justfile's managed_python). Its macOS is
    # named by its version, never macos-latest, which GitHub moves under a workflow; its Linux is the one every other
    # job runs on.
    requires = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["requires-python"]
    floor = re.fullmatch(r">=([0-9]+\.[0-9]+)", requires)
    assert floor, requires
    action = yaml.safe_load((REPO / ".github" / "actions" / "setup" / "action.yml").read_text())
    default = str(action["inputs"]["python-version"]["default"])
    engine = JOBS["engine"]
    # The job whole, with no key of its own beyond these: an if: but the area stack's gate would skip it (on pull
    # requests, say), which CI result takes as passed; an env: (a PYTEST_ADDOPTS) would narrow the suite it runs; and
    # without its limit a job hung on a macOS runner would hold it for GitHub's 360 minutes.
    assert set(engine) == {"name", "if", "runs-on", "timeout-minutes", "strategy", "steps"}, sorted(engine)
    assert engine["if"] == STACK_GATE
    assert engine["timeout-minutes"] == 30
    assert set(engine["strategy"]) == {"fail-fast", "matrix"}, engine["strategy"]
    assert engine["name"] == "Engine tests (${{ matrix.os }}, Python ${{ matrix.python }})"
    assert engine["runs-on"] == "${{ matrix.os }}"
    assert engine["strategy"]["fail-fast"] is False
    matrix = engine["strategy"]["matrix"]
    assert set(matrix) == {"os", "python"}, matrix
    assert sorted(matrix["python"]) == sorted({floor[1], default}) and len(matrix["python"]) == 2, matrix["python"]
    linux, macos = matrix["os"]
    assert linux == JOBS["unit"]["runs-on"], matrix["os"]
    assert MACOS_BY_VERSION.fullmatch(macos), macos
    # Each step as the unit job's: its checkout, the setup action given the matrix's Python, then the suite; before
    # it, on Linux, the image of the guest helper's floor pulled, so that its test runs there and is not skipped (the
    # test never pulls; macOS's runner has no docker).
    checkout, setup, pull, run = engine["steps"]
    assert checkout == JOBS["unit"]["steps"][0]
    assert setup == {"uses": "./.github/actions/setup", "with": {"python-version": "${{ matrix.python }}"}}
    assert pull == {
        "name": "Pull the guest helper's floor",
        "if": "runner.os == 'Linux'",
        "run": f"docker pull {_helper_floor_image()}",
    }
    assert run == {"name": "Engine tests", "run": "just test engine"}


def _helper_floor_image() -> str:
    """The image tests/unit/test_aw_guest_helper.py runs the helper's floor on, its IMAGE, read without importing
    it."""
    tree = ast.parse((REPO / "tests" / "unit" / "test_aw_guest_helper.py").read_text())
    (image,) = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and [getattr(target, "id", None) for target in node.targets] == ["IMAGE"]
    ]
    return image


def test_nothing_at_the_workflow_s_level_narrows_or_skips_the_jobs_it_runs():
    # Above every job's keys, the workflow's own: its env: reaches each job (a PYTEST_ADDOPTS there would narrow the
    # engine's suite and the unit shards with every test green), its defaults: would run each step under another
    # shell, and an on: without pull_request, or one that filters them, would leave CI result unsaid on a pull request.
    assert set(WORKFLOW["env"]) == {"PY_COLORS", "ANSIBLE_FORCE_COLOR"}, WORKFLOW["env"]
    assert "defaults" not in WORKFLOW, WORKFLOW["defaults"]
    assert "pull_request" in WORKFLOW["on"], WORKFLOW["on"]
    assert WORKFLOW["on"]["pull_request"] is None, WORKFLOW["on"]["pull_request"]


@pytest.mark.parametrize("runs_on", ["macos-latest", "macos-latest-large", "macos-26-xlarge", "macos-26\n", "macos"])
def test_a_macos_runner_named_by_anything_but_its_version_is_not_taken(runs_on):
    # The names GitHub gives the runners that move under a workflow (latest) or are larger, paid ones.
    assert not MACOS_BY_VERSION.fullmatch(runs_on)


def test_the_canary_runs_the_pinned_image_unpinned():
    (image,) = {image for each in _molecule_images().values() for image in each}
    assert JOBS["canary"]["env"]["MOLECULE_IMAGE"] == IMAGE.fullmatch(image)["repository"] + ":latest"


# ------------------------------------------------------------------------------- the Molecule containers' boot ---

BOOT_WAIT = REPO / "tests" / "molecule" / "boot_wait.yml"
NEEDS_TIMEOUT = pytest.mark.skipif(shutil.which("timeout") is None, reason="coreutils' timeout is not installed")


def _plays(playbook: Path) -> list[tuple[Path, dict]]:
    """Each play a playbook runs, in order, with the file it is in: an imported playbook's plays in its place."""
    plays = []
    for play in yaml.safe_load(playbook.read_text()):
        imported = play.get("ansible.builtin.import_playbook") or play.get("import_playbook")
        plays += _plays((playbook.parent / imported).resolve()) if imported else [(playbook, play)]
    return plays


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda path: str(path.relative_to(REPO)))
def test_every_scenario_waits_for_its_containers_to_boot_before_a_module_runs_there(scenario):
    # A container's boot empties /tmp, where a module's payload is unpacked, and Molecule's create returns before the
    # boot is over: five scenarios lost their first module's payload on a busy CI round. So prepare comes straight
    # after create in every sequence the scenario has - Molecule's own, the converge and create ones too, and any it
    # sets - its own prepare.yml is the playbook prepare runs, and the first play of that, an import followed, is the
    # wait. The image scenario's prepare.yml imports the configure scenario's, and so waits through it.
    config = yaml.safe_load((scenario / "molecule.yml").read_text())
    sequences = {**DEFAULT_CONFIG["scenario"], **SHARED_MOLECULE.get("scenario", {}), **config.get("scenario", {})}
    for name, sequence in sequences.items():
        if name.endswith("_sequence") and "create" in sequence:
            assert sequence[sequence.index("create") + 1] == "prepare", (name, sequence)
    for provisioner in (SHARED_MOLECULE.get("provisioner", {}), config.get("provisioner", {})):
        assert "prepare" not in provisioner.get("playbooks", {}), provisioner["playbooks"]
    prepare = scenario / "prepare.yml"
    assert prepare.is_file(), f"{prepare.relative_to(REPO)}: a scenario prepares, and waits there first"
    (first, play), *_ = _plays(prepare)
    assert first == BOOT_WAIT, f"{prepare.relative_to(REPO)} runs {play.get('name')!r} before the boot wait"


def test_the_boot_wait_runs_no_module_in_a_container():
    # Gathering facts runs the setup module, the very one CI saw lose its payload; raw sends none, assert runs on the
    # controller, and a play with nothing but these two tasks has nowhere to put another.
    (play,) = yaml.safe_load(BOOT_WAIT.read_text())
    assert set(play) == {"name", "hosts", "gather_facts", "tasks"}
    assert play["hosts"] == "all"
    assert play["gather_facts"] is False
    wait, said = play["tasks"]
    assert [key for key in wait if "." in key] == ["ansible.builtin.raw"]
    assert [key for key in said if "." in key] == ["ansible.builtin.assert"]
    # Never changed, for an idempotence stage; a boot that is over is running, or degraded where a unit failed.
    assert wait["changed_when"] is False
    assert said["ansible.builtin.assert"]["that"] == f"({wait['register']}.stdout | trim) in ['running', 'degraded']"


def _boot_wait(tmp_path: Path, pid1: str, answers: list[str], seconds: int = 120) -> subprocess.CompletedProcess:
    """The wait's own script, run by sh against a stand-in systemctl that gives the next of its answers at each call
    and its last for good, `nobus` as systemctl says it before systemd's socket is up; PID 1's name read from a file of
    the test's in place of /proc/1/comm, the 120 s cut to `seconds`, and each second between two calls to 0.05 s."""
    (play,) = yaml.safe_load(BOOT_WAIT.read_text())
    script = play["tasks"][0]["ansible.builtin.raw"]
    assert script.count("/proc/1/comm") == 1 and script.count("timeout 120 ") == 1
    (tmp_path / "comm").write_text(pid1 + "\n")
    script = script.replace("/proc/1/comm", str(tmp_path / "comm")).replace("timeout 120 ", f"timeout {seconds} ")
    (tmp_path / "answers").write_text("".join(f"{answer}\n" for answer in answers))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$BOOT_CALLS"\n'
        'n=$(( $(wc -l < "$BOOT_CALLS") )); last=$(( $(wc -l < "$BOOT_ANSWERS") ))\n'
        '[ "$n" -le "$last" ] || n=$last\n'
        'answer=$(sed -n "${n}p" "$BOOT_ANSWERS")\n'
        "case $answer in\n"
        'nobus) echo "Failed to connect to bus: No such file or directory" >&2; exit 1 ;;\n'
        "running) echo running ;;\n"
        '*) echo "$answer"; exit 1 ;;\n'
        "esac\n"
    )
    (bin_dir / "sleep").write_text("#!/bin/sh\nexec /bin/sleep 0.05\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BOOT_CALLS": str(tmp_path / "calls"),
        "BOOT_ANSWERS": str(tmp_path / "answers"),
    }
    return subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True, timeout=60)


@NEEDS_TIMEOUT
@pytest.mark.parametrize(
    ("pid1", "answers", "seconds", "said", "waits"),
    [
        # Asked at the very start of a boot, systemctl cannot reach systemd, then says offline: asked again each time.
        pytest.param("systemd", ["nobus", "offline", "degraded"], 120, "degraded", 3, id="early"),
        pytest.param("systemd", ["running"], 120, "running", 1, id="booted"),
        # A boot not over in time: what systemd says then, which the assert refuses.
        pytest.param("systemd", ["starting"], 1, "starting", None, id="too-slow"),
        # No systemd at all, as a platform with the driver's own command would run: said at once, systemctl unasked.
        pytest.param("bash", ["running"], 120, "PID 1 is bash, not systemd", 0, id="no-systemd"),
    ],
)
def test_the_boot_wait_asks_systemd_until_it_says_the_boot_is_over(tmp_path, pid1, answers, seconds, said, waits):
    run = _boot_wait(tmp_path, pid1, answers, seconds)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == said, (run.stdout, run.stderr)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    if waits is None:
        assert len(calls) > 2 and set(calls[:-1]) == {"is-system-running --wait"}, calls
        assert calls[-1] == "is-system-running", calls
    else:
        assert calls == ["is-system-running --wait"] * waits + ["is-system-running"] * bool(waits), calls


# ----------------------------------------------------------------------- pull requests' labels, and releases ---

LABELS = yaml.safe_load((REPO / ".github" / "workflows" / "labels.yml").read_text())
RELEASE = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
NOTES = yaml.safe_load((REPO / ".github" / "release.yml").read_text())


def _title_tags() -> list[str]:
    """The tags CONTRIBUTING.md asks a pull request's title to start with, in its order."""
    text = (REPO / "CONTRIBUTING.md").read_text()
    paragraph = text[text.index("**PR titles**") :].split("\n\n", 1)[0]
    return list(dict.fromkeys(re.findall(r"`\[(\w+)\]", paragraph)))


def _step(workflow: dict, job: str) -> str:
    (step,) = [step for step in workflow["jobs"][job]["steps"] if "run" in step]
    return step["run"]


def _stand_in_gh(tmp_path: Path) -> Path:
    """A gh that records each call, a line of its arguments, and does nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_CALLS"\n')
    (bin_dir / "gh").chmod(0o755)
    return bin_dir


def _run(script: str, tmp_path: Path, cwd: Path, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    """script as GitHub runs a step's on ubuntu-latest, bash -e with pipefail, and the calls it made of gh."""
    calls = tmp_path / "gh-calls"
    calls.unlink(missing_ok=True)
    if not (tmp_path / "bin").exists():
        _stand_in_gh(tmp_path)
    path = [str(tmp_path / "bin")]
    path += [str(Path(found).parent) for found in (shutil.which("jq"), shutil.which("git")) if found]
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        env={"PATH": ":".join([*path, "/usr/bin", "/bin"]), "GH_CALLS": str(calls), "HOME": str(tmp_path), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    return run, calls.read_text().splitlines() if calls.exists() else []


def _label(tmp_path: Path, title: str, labels=(), author: str = "octocat"):
    env = {"REPO": "o/r", "NUMBER": "7", "TITLE": title, "AUTHOR": author, "LABELS": json.dumps(list(labels))}
    return _run(_step(LABELS, "label"), tmp_path, tmp_path, **env)


def test_the_labels_are_written_by_the_base_branchs_workflow_with_nothing_of_the_pull_requests():
    # pull_request_target hands a token that writes: nothing of the pull request may be checked out, and its title
    # reaches the script as a variable, never inside it.
    assert set(LABELS["on"]) == {"pull_request_target"}
    assert LABELS["permissions"] == {"pull-requests": "write"}
    (step,) = LABELS["jobs"]["label"]["steps"]
    assert "uses" not in step and "${{" not in step["run"]
    assert step["env"]["TITLE"] == "${{ github.event.pull_request.title }}"


@NEEDS_JQ
def test_every_title_tag_is_a_label_and_every_label_a_section_of_the_notes(tmp_path):
    tags = _title_tags()
    assert tags == ["feat", "fix", "cleanup", "docs", "test", "ci", "build"], tags
    sections = [category["labels"] for category in NOTES["changelog"]["categories"]]
    assert sections[-1] == ["*"], "the last section takes every pull request no other one does"
    for tag in tags:
        run, calls = _label(tmp_path, f"[{tag}] A change")
        assert run.returncode == 0, run.stderr
        (call,) = calls
        label = call.rsplit("labels[]=", 1)[1]
        assert any(label in labels for labels in sections[:-1]), (tag, label)


@NEEDS_JQ
@pytest.mark.parametrize(
    ("title", "labels", "calls"),
    [
        ("[feat] Draw just test live", [], ["POST repos/o/r/issues/7/labels -f labels[]=enhancement"]),
        # A stacked pull request's title: its first kind of change.
        ("[1/2] [VSCodeRemote] [feat] open each VM", [], ["POST repos/o/r/issues/7/labels -f labels[]=enhancement"]),
        # Retagged: the old tag's label goes, and a label of another kind stays.
        (
            "[fix] Hold Ctrl-C off",
            ["enhancement", "python"],
            ["DELETE repos/o/r/issues/7/labels/enhancement", "POST repos/o/r/issues/7/labels -f labels[]=bug"],
        ),
        ("[docs] Show just status", ["documentation"], []),
        ("[ci] Shard the unit tests", [], ["POST repos/o/r/issues/7/labels -f labels[]=ci"]),
    ],
)
def test_a_pull_request_is_labelled_as_its_titles_tag_says(tmp_path, title, labels, calls):
    run, made = _label(tmp_path, title, labels)
    assert run.returncode == 0, run.stderr
    assert made == [f"api --method {call}" for call in calls]


@NEEDS_JQ
def test_a_title_without_a_tag_is_warned_of_and_its_old_label_taken_off(tmp_path):
    run, made = _label(tmp_path, "Draw just test live", ["enhancement"])
    assert run.returncode == 0, run.stderr
    assert made == ["api --method DELETE repos/o/r/issues/7/labels/enhancement"]
    assert "::warning::The title has none of [feat]" in run.stdout


@NEEDS_JQ
def test_dependabots_pull_requests_are_left_as_it_labels_them(tmp_path):
    run, made = _label(tmp_path, "build(deps): Bump ruff from 0.16.10 to 0.17.0", ["dependencies"], "dependabot[bot]")
    assert run.returncode == 0 and made == [] and run.stdout == "", (run.stdout, run.stderr)


@NEEDS_JQ
def test_a_title_is_never_run(tmp_path):
    title = '[feat] $(touch ran-1) `touch ran-2` "; touch ran-3; "'
    run, made = _label(tmp_path, title)
    assert run.returncode == 0, run.stderr
    assert made == ["api --method POST repos/o/r/issues/7/labels -f labels[]=enhancement"]
    assert not list(tmp_path.glob("ran-*"))


def _git(cwd: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), **GIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


@pytest.fixture(scope="module")
def tagged(tmp_path_factory) -> Path:
    """An origin with main, a branch off it, and a tag of each kind; and a clone of it as actions/checkout leaves
    one: every branch and tag fetched, each tag lightweight, since it fetches a pushed tag by its commit."""
    root = tmp_path_factory.mktemp("release")
    origin, work, checkout = root / "origin.git", root / "work", root / "checkout"
    _git(root, "init", "--quiet", "--bare", "--initial-branch=main", str(origin))
    _git(root, "clone", "--quiet", str(origin), str(work))
    _git(work, "commit", "--quiet", "--allow-empty", "-m", "one")
    _git(work, "push", "--quiet", "origin", "HEAD:main")
    _git(work, "tag", "-a", "v0.1.0", "-m", "First release\n\nEvery VM in one command.")
    _git(work, "tag", "-a", "v1.0.0-rc.1", "-m", "The first candidate")
    _git(work, "tag", "-a", "v1.2", "-m", "Two parts")
    _git(work, "tag", "v0.2.0")
    _git(work, "checkout", "--quiet", "-b", "side")
    _git(work, "commit", "--quiet", "--allow-empty", "-m", "off main")
    _git(work, "push", "--quiet", "origin", "side")
    _git(work, "tag", "-a", "v0.3.0", "-m", "Off main")
    _git(work, "push", "--quiet", "origin", "--tags")
    _git(root, "clone", "--quiet", str(origin), str(checkout))
    for tag in _git(checkout, "tag", "--list").split():
        _git(checkout, "tag", "--force", tag, f"{tag}^{{commit}}")
    return checkout


def _release(tmp_path: Path, checkout: Path, tag: str):
    runner = tmp_path / "runner"
    runner.mkdir()
    run, calls = _run(_step(RELEASE, "release"), tmp_path, checkout, TAG=tag, RUNNER_TEMP=str(runner))
    _git(checkout, "tag", "--force", tag, f"{tag}^{{commit}}")  # as the next run's checkout finds it
    return run, calls, runner


def test_an_annotated_semver_tag_on_main_is_released_its_message_first(tmp_path, tagged):
    run, calls, runner = _release(tmp_path, tagged, "v0.1.0")
    assert run.returncode == 0, (run.stdout, run.stderr)
    notes = runner / "notes.md"
    assert calls == [f"release create v0.1.0 --verify-tag --title v0.1.0 --notes-file {notes} --generate-notes"]
    assert notes.read_text().startswith("First release\n\nEvery VM in one command.")


def test_a_prerelease_is_released_as_one(tmp_path, tagged):
    run, calls, _ = _release(tmp_path, tagged, "v1.0.0-rc.1")
    assert run.returncode == 0, (run.stdout, run.stderr)
    (call,) = calls
    assert call.startswith("release create v1.0.0-rc.1 ") and call.endswith(" --generate-notes --prerelease")


@pytest.mark.parametrize(
    ("tag", "said"),
    [
        ("v0.2.0", "is a lightweight tag"),
        ("v0.3.0", "is on a commit main does not have"),
        ("v1.2", "is not vMAJOR.MINOR.PATCH"),
    ],
)
def test_a_tag_that_is_no_release_is_refused_before_anything_is_published(tmp_path, tagged, tag, said):
    run, calls, _ = _release(tmp_path, tagged, tag)
    assert run.returncode == 1 and calls == [], (run.stdout, run.stderr, calls)
    assert f"::error::{tag} {said}" in run.stdout


# ----------------------------------------------------------------------------- what a version promises about ---

# The justfile's settings that are no part of the interface: where a run keeps its working files.
UNPROMISED_SETTINGS = {"STATUS_DIR", "LIVE_DIR"}


def _promised(pattern: str) -> set[str]:
    """The names in backticks of the part of CONTRIBUTING.md's Releasing section that pattern's group holds."""
    text = (REPO / "CONTRIBUTING.md").read_text()
    section = text[text.index("## Releasing") :]
    return set(re.findall(r"`([\w-]+)`", re.search(pattern, section, re.S).group(1)))


def _recipes(group: str) -> set[str]:
    """The justfile's recipes in a group: each recipe's [group] attribute sits in the lines above its header."""
    found, attributes = set(), []
    for line in (REPO / "justfile").read_text().splitlines():
        if line.startswith("["):
            attributes.append(line)
        elif header := re.match(r"^([a-z][\w-]*)\b(?! *:=)[^#]*:$", line):
            if f"[group('{group}')]" in attributes:
                found.add(header.group(1))
            attributes = []
    return found


def test_the_interface_a_version_promises_is_the_justfiles():
    """CONTRIBUTING.md names what a version number promises about - the recipes people use, the roles --tags takes,
    the settings every recipe reads - and what it does not, the contributor recipes. A recipe added to the justfile's
    use group, a role to configure_roles or a setting read from the environment would otherwise fall outside the
    promise without a word."""
    justfile = (REPO / "justfile").read_text()
    assert _promised(r"the `just` commands people use - (.*?) - and their options") == _recipes("use") | {"help"}
    assert _promised(r"The contributor commands - (.*?) - the justfile's other settings") == _recipes("develop")
    roles = re.search(r"^configure_roles := '([^']*)'$", justfile, re.M).group(1).split()
    assert _promised(r"the role names `just deploy --tags` takes: (.*?);") == set(roles)
    settings = set(re.findall(r"^([A-Z_]+) := env\('\1'", justfile, re.M))
    assert _promised(r"from the environment or before its name: (.*?);") == settings - UNPROMISED_SETTINGS


def test_no_checkout_leaves_the_workflow_s_token_in_git_s_config():
    # actions/checkout writes the job's token into .git/config unless told not to, where every later step - and
    # whatever a test, a hook or a dependency runs - can read it. No step after a checkout fetches or pushes.
    checkouts = []
    for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        for name, job in yaml.safe_load(workflow.read_text())["jobs"].items():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    checkouts.append((workflow.name, name, (step.get("with") or {}).get("persist-credentials")))
    assert checkouts and all(kept is False for _, _, kept in checkouts), checkouts
