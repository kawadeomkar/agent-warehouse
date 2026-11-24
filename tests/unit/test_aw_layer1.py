"""Layer 1's child (lib/claude_vm/guest/layer1.py; docs/architecture/engine.md §16.10).

Every run here is a real ansible-playbook, started by layer1.run as a child of a job of this process's own, against a
stand-in machine (tests/unit/aw_machine.py): a play of one task over the test-only local connection, which touches only
the machine's directory, or a play of one ping over Ansible's own ssh connection to the stand-in ssh, which records the
words it was given. The generated configuration read back by ansible-config is a fixed expectation; a play of one ping
runs, its child in a session of its own and in the run's registry with its pid, with nothing on this process's
terminal; the caller's ANSIBLE_*, PYTHON* and an exported stdout callback never reach the child, whose environment is
the allow list and the engine's own; a target that is no machine's, a first use's, one with no key filed, an inventory
name that is not an identity, and every other value that is not one are refused before anything is written or started;
SIGINT to the child's group ends it, and proc.end leaves nothing of it; a call cancelled ends the child with its tree;
and the host's ansible_ssh_common_args start with the runner's own, StrictHostKeyChecking=yes against the product's
known-hosts file, which ssh -G shows holding over a Host * that loosens them, in the very words Ansible gave ssh. The
rules with a test of their own: the argument list never holds key=value after -e, and no product code passes the
test-only connection.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_machine import Machine, layer1_host, machine, ssh_stand_in, target
from harness import REPO, repo_files

from claude_vm import checks, proc
from claude_vm.fleet import paths
from claude_vm.guest import events, layer1
from claude_vm.runs import children, rundir
from claude_vm.runs import job as jobs
from claude_vm.schema import printer
from claude_vm.ssh import hostkeys, runner

LAYER1 = REPO / "lib" / "claude_vm" / "guest" / "layer1.py"
# What the generated configuration sets, as ansible-config reads it back: every setting it changes but the two whose
# values are paths of the run's own (CONFIG_FILE, DEFAULT_LOCAL_TMP), which the test reads apart.
EXPECTED = {
    "ANSIBLE_PIPELINING": True,
    "DEFAULT_CALLBACK_PLUGIN_PATH": [str(layer1.CALLBACKS)],
    "DEFAULT_FORKS": 1,
    "DEFAULT_ROLES_PATH": [str(layer1.ROLES)],
    "DEFAULT_STDOUT_CALLBACK": layer1.CALLBACK,
    "INJECT_FACTS_AS_VARS": False,
    "INVENTORY_ENABLED": ["yaml"],
}
# What the child's environment sets beside it, as Ansible reads it: the untrusted-template switch, by its variable.
FROM_THE_ENVIRONMENT = {"_TEMPLAR_UNTRUSTED_TEMPLATE_BEHAVIOR": "error"}
# The caller's variables a child must never be given, beside its own: Ansible's, Python's, a stdout callback exported,
# and one of no tool's.
CANARIES = {
    "ANSIBLE_STDOUT_CALLBACK": "json",
    "ANSIBLE_F3_CANARY": "1",
    "PYTHONF3CANARY": "1",
    "PYTHONPATH": "/nowhere",
    "F3_CANARY": "1",
}
# What a person's own ssh configuration could say for every host, to loosen the key's checking, take a prompt, make a
# master, forward an agent: what the child's own options hold over.
LOOSENING = """Host *
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  HostKeyAlias loosened
  BatchMode no
  ControlMaster auto
  ControlPersist yes
  ForwardAgent yes
  CheckHostIP yes
  UpdateHostKeys yes
"""


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that starts layer 1 has; both gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "machines"


def _filed(made_machine: Machine) -> Machine:
    """made_machine's own key filed under its identity in the known-hosts file its target checks, as G3 files a VM's:
    the stand-in ssh checks it there, as ssh does."""
    hostkeys.file(target(made_machine).known_hosts, made_machine.identity, hostkeys.parse(made_machine.key))
    return made_machine


def _alpha(root: Path) -> Machine:
    return _filed(machine(root, "alpha"))


def _play(folder: Path, *tasks: dict) -> Path:
    """A play of tasks over every host of the inventory, its facts not gathered, written as JSON (which YAML reads)."""
    folder.mkdir(parents=True, exist_ok=True)
    play = folder / "play.yml"
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": list(tasks)}]))
    return play


PING = {"name": "ping", "ansible.builtin.ping": {}}


def _run(job: jobs.Job, at: runner.Target, play: Path, **given) -> layer1.Ended:
    given.setdefault("env", dict(os.environ))
    return asyncio.run(layer1.run(job, at, play, name="alpha", host_flag="", **given))


def _written(job: jobs.Job) -> list[str]:
    directory = rundir.made(job)
    assert directory is not None
    return sorted(path.name for path in directory.iterdir())


def _inventory(job: jobs.Job, made_machine: Machine) -> dict:
    directory = rundir.made(job)
    assert directory is not None
    return json.loads((directory / f"{layer1.INVENTORY_PREFIX}{made_machine.identity}.yml").read_text())


def _first(words: list[str]) -> dict[str, str]:
    """The value ssh keeps for each -o option of words, by its name in lower case: the first given."""
    found: dict[str, str] = {}
    for at, word in enumerate(words[:-1]):
        if word == "-o":
            name, _, value = words[at + 1].replace("=", " ", 1).partition(" ")
            found.setdefault(name.lower(), value)
    return found


def _still(remembered: dict[int, str]) -> list[int]:
    """The remembered processes still at their pids, each the process of its start, once a moment has let an end
    land."""
    deadline = time.monotonic() + 10
    while True:
        found = []
        for pid, started in remembered.items():
            shown = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
            if shown.returncode == 0 and shown.stdout.strip() == started:
                found.append(pid)
        if not found or time.monotonic() > deadline:
            return found
        time.sleep(0.1)


# ---------------------------------------------------------------- the configuration ----


def test_the_generated_configuration_read_back_is_the_fixed_expectation(made) -> None:
    directory = rundir.made(made)
    assert directory is not None
    config = directory / layer1.CONFIG
    config.write_text(layer1.configuration_text(directory))
    # In the child's own environment, as a child of this run would read it.
    shown = subprocess.run(
        [
            str(Path(sys.executable).with_name("ansible-config")),
            "dump",
            "-c",
            str(config),
            "--only-changed",
            "-f",
            "json",
        ],
        env=layer1.environment(os.environ, config, 3),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    settings = {entry["name"]: entry for entry in json.loads(shown.stdout) if "name" in entry}
    assert settings.pop("CONFIG_FILE")["value"] == str(config)
    local_tmp = settings.pop("DEFAULT_LOCAL_TMP")
    # Ansible makes a folder of its own for each process under the configured one.
    assert Path(local_tmp["value"]).parent == directory / layer1.LOCAL_TMP, local_tmp
    assert {name: entry["value"] for name, entry in settings.items()} == {**EXPECTED, **FROM_THE_ENVIRONMENT}
    origins = {name: entry["origin"] for name, entry in settings.items()}
    assert {origins.pop(name) for name in FROM_THE_ENVIRONMENT} == {f"env: {layer1.UNTRUSTED_VARIABLE}"}
    assert {local_tmp["origin"], *origins.values()} == {str(config)}
    assert layer1.ROLES == REPO.resolve() / "roles"


def test_the_configuration_sets_ssh_args_empty_and_names_no_collections_path(made) -> None:
    directory = rundir.made(made)
    assert directory is not None
    lines = layer1.configuration_text(directory).splitlines()
    # Empty, set: Ansible's own default would put ControlMaster=auto ahead of the host's options.
    assert lines[lines.index("[ssh_connection]") + 1] == "ssh_args ="
    assert not [line for line in lines if line.startswith(("collections_path", "callbacks_enabled"))]
    # The product's callback, by its folder beside the engine, and no other folder of callbacks.
    assert [line for line in lines if line.startswith("callback_plugins")] == [f"callback_plugins = {layer1.CALLBACKS}"]
    assert (layer1.CALLBACKS / f"{layer1.CALLBACK}.py").is_file()


# ---------------------------------------------------------------- a play runs ----


def test_a_play_of_one_ping_runs_through_layer1_on_a_stand_in_its_child_in_the_registry(made, root, tmp_path, capfd):
    alpha = _alpha(root)
    seen: dict[str, object] = {}

    def started(process) -> None:
        seen["pid"] = process.pid
        seen["registry"] = [(child.kind, child.pid) for child in children.live(made)]
        seen["session"], seen["group"] = os.getsid(process.pid), os.getpgid(process.pid)
        fds = Path(f"/proc/{process.pid}/fd")
        if fds.is_dir():
            seen["stdin"] = os.readlink(fds / "0")
            seen["events"] = stat.S_ISFIFO(
                os.stat(fds / str(int(_environ(process.pid)[layer1.EVENTS_VARIABLE]))).st_mode
            )

    ended = _run(made, target(alpha), _play(tmp_path, PING), started=started, test_connection=layer1_host(alpha))
    assert ended.status == 0, ended.said
    assert ended.counts == events.Counts(failed=0, unreachable=0, changed=0, recap=True), ended.said
    # In the run's registry with its pid, while it ran; in a session of its own, so its group's leader.
    assert seen["registry"] == [(children.ANSIBLE, seen["pid"])]
    assert seen["session"] == seen["group"] == seen["pid"]
    if "stdin" in seen:
        assert (seen["stdin"], seen["events"]) == ("/dev/null", True)
    assert children.live(made) == []
    # What it printed went to its own file, never this process's terminal; and its events to the run, never there.
    out, err = capfd.readouterr()
    assert "PLAY" not in out + err and "ping" not in out + err
    assert "PLAY" not in ended.said, ended.said
    # The configuration and the one host's inventory, in the run's directory, at 0600.
    directory = rundir.made(made)
    assert directory is not None
    assert (directory / layer1.CONFIG).read_text() == layer1.configuration_text(directory)
    inventory = f"{layer1.INVENTORY_PREFIX}{alpha.identity}{layer1.INVENTORY_SUFFIX}"
    assert [name for name in _written(made) if not name.startswith(layer1.LOCAL_TMP)] == [inventory, layer1.CONFIG]
    for name in (inventory, layer1.CONFIG):
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600, name
    [(name, host)] = _inventory(made, alpha)["all"]["hosts"].items()
    # Named by its identity; the name a person chose a variable of it.
    assert (name, host[layer1.NAME_VARIABLE]) == (alpha.identity, "alpha")
    # The test's own connection over the product's: its interpreter, its local connection, become off.
    over = layer1_host(alpha)
    assert {name: host[name] for name in over} == over


def _environ(pid: int) -> dict[str, str]:
    """A process's environment as Linux shows it."""
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    return dict(item.split("=", 1) for item in raw.decode().split("\0") if "=" in item)


@pytest.mark.parametrize("verbosity", [0, 2])
def test_the_child_s_environment_is_the_allow_list_and_the_engine_s_own(made, root, tmp_path, verbosity) -> None:
    alpha = _alpha(root)
    # A task's module inherits the child's environment, under the stand-in's interpreter, which sets HOME and PATH.
    listed = {
        "name": "environment",
        "ansible.builtin.command": {"argv": ["/bin/sh", "-c", "env > env.txt"], "chdir": str(alpha.home)},
    }
    agent = str(tmp_path / "agent.sock")
    given = {**os.environ, **CANARIES, "SSH_AUTH_SOCK": agent}
    heard: list[jobs.Event] = []
    made.listen(heard.append)
    ended = _run(
        made,
        target(alpha),
        _play(tmp_path, listed),
        env=given,
        verbosity=verbosity,
        test_connection=layer1_host(alpha),
    )
    assert ended.status == 0, ended.said
    found = dict(line.split("=", 1) for line in (alpha.home / "env.txt").read_text().splitlines() if "=" in line)
    assert set(CANARIES).isdisjoint(found), sorted(set(CANARIES) & set(found))
    directory = rundir.made(made)
    assert directory is not None
    assert found[layer1.CONFIG_VARIABLE] == str(directory / layer1.CONFIG)
    assert found[layer1.UNTRUSTED_VARIABLE] == "error"
    assert found[layer1.EVENTS_VARIABLE].isdigit()
    assert found[children.MARKER] == made.id
    assert found["SSH_AUTH_SOCK"] == agent
    assert found.get(layer1.VERBOSITY_VARIABLE) == (str(verbosity) if verbosity else None)
    # The stdout callback exported never reached it: the product's wrote the play's events, and nothing printed JSON.
    assert [event.data["status"] for event in heard] == ["play", "task", "changed", "recap"], heard
    assert not ended.said.lstrip().startswith("{"), ended.said


def test_the_environment_keeps_only_the_allow_list_of_the_caller_s(tmp_path) -> None:
    given = {**CANARIES, "PATH": "/bin", "HOME": "/home/x", "LANG": "C.UTF-8", "SSH_AUTH_SOCK": "/s", "TERM": "xterm"}
    built = layer1.environment(given, tmp_path / layer1.CONFIG, 7)
    assert built == {
        "PATH": "/bin",
        "HOME": "/home/x",
        "LANG": "C.UTF-8",
        "SSH_AUTH_SOCK": "/s",
        layer1.CONFIG_VARIABLE: str(tmp_path / layer1.CONFIG),
        layer1.UNTRUSTED_VARIABLE: "error",
        layer1.EVENTS_VARIABLE: "7",
    }
    assert layer1.environment({}, tmp_path / layer1.CONFIG, 7, 5)[layer1.VERBOSITY_VARIABLE] == "5"


# ---------------------------------------------------------------- what is refused before anything starts ----


def _forged(at: runner.Target, identity: str) -> runner.Target:
    """at with another identity, past what a Target holds: what layer 1 holds again by itself."""
    object.__setattr__(at, "identity", identity)
    return at


@pytest.mark.parametrize(
    ("case", "said"),
    [
        ("the proxmox host", "the Proxmox host is no machine of layer 1's"),
        ("an identity that is not one", "an inventory names a machine by its identity"),
        ("a first use", "a first use's target"),
        ("a play that is not there", "the play, "),
        ("a play by a relative path", "the play is an absolute path"),
        ("a variables file that is not there", "the variables file, "),
        ("a part that is no role's name", "each part asked for is a role's name"),
        ("a part twice", "each part asked for is a role's name, once"),
        ("the parts as one string", "the parts asked for are a list of names"),
        ("check as a word", "check is True or False"),
        ("a verbosity past five", "the verbosity is a count from 0 to 5"),
        ("a verbosity of True", "the verbosity is a count from 0 to 5"),
        ("two machines' names", "a machine's name is one VM name"),
        ("a host's flag the printer refuses", "the line that files a machine's key cannot be made"),
        ("a test connection over ssh", "a test-only connection is one of local, community.docker.docker"),
        ("a test connection that gives more", "a test-only connection gives no ansible_ssh_common_args"),
        ("an option that opens a template", "the inventory's ansible_ssh_common_args would open a template"),
        ("an environment that is no mapping", "the run's environment is a mapping"),
        ("a redaction list of plain words", "the run's redaction list holds Secrets alone"),
        ("a redaction list that is one word", "the run's redaction list is a list of Secrets"),
    ],
)
def test_what_is_not_one_is_refused_before_anything_is_written_or_started(made, root, tmp_path, case, said) -> None:
    alpha = _alpha(root)
    play = _play(tmp_path, PING)
    at = target(alpha)
    given: dict[str, object] = {"name": "alpha", "host_flag": "", "env": dict(os.environ)}
    variables: Path | None = None
    if case == "the proxmox host":
        at = runner.proxmox_host("198.51.100.4", "root", "/aw-machines/cp")
    elif case == "an identity that is not one":
        at = _forged(at, "pve")
    elif case == "a first use":
        at = target(alpha, first_use=True, known_hosts=tmp_path / "scratch")
    elif case == "a play that is not there":
        play = tmp_path / "absent.yml"
    elif case == "a play by a relative path":
        play = Path("play.yml")
    elif case == "a variables file that is not there":
        variables = tmp_path / "absent.json"
    elif case == "a part that is no role's name":
        given["tags"] = ["common", "a,b"]
    elif case == "a part twice":
        given["tags"] = ["common", "common"]
    elif case == "the parts as one string":
        given["tags"] = "common"
    elif case == "check as a word":
        given["check"] = "yes"
    elif case == "a verbosity past five":
        given["verbosity"] = 6
    elif case == "a verbosity of True":
        given["verbosity"] = True
    elif case == "two machines' names":
        given["name"] = "alpha,beta"
    elif case == "a host's flag the printer refuses":
        given["host_flag"] = None
    elif case == "a test connection over ssh":
        given["test_connection"] = {"ansible_connection": "ssh"}
    elif case == "a test connection that gives more":
        given["test_connection"] = {"ansible_connection": "local", "ansible_ssh_common_args": "-o X=y"}
    elif case == "an option that opens a template":
        at = target(alpha, given={"Compression": "{{ lookup('pipe', 'id') }}"})
    elif case == "an environment that is no mapping":
        given["env"] = ["PATH=/bin"]
    elif case == "a redaction list of plain words":
        given["redacted"] = ["a secret as a plain word"]
    elif case == "a redaction list that is one word":
        given["redacted"] = "a secret as a plain word"
    with pytest.raises(layer1.Refused) as refused:
        asyncio.run(layer1.run(made, at, play, variables, **given))
    assert said in str(refused.value)
    assert "lookup" not in str(refused.value)
    assert _written(made) == [] and children.live(made) == []


def test_a_machine_with_no_key_filed_is_refused_by_name_with_the_line_that_files_it(made, root, tmp_path) -> None:
    alpha = machine(root, "alpha")
    at = target(alpha)
    with pytest.raises(layer1.NoKey) as refused:
        _run(made, at, _play(tmp_path, PING), test_connection=layer1_host(alpha))
    files = printer.line("deploy", {"names": ["alpha"], "only": "provision"}, refused=True, host_flag="")
    assert str(refused.value) == (
        f"no host key of {alpha.identity} is filed in {at.known_hosts}, and layer 1 never takes one: a machine's key is"
        f" filed at its first connection, before its first configure, which {files} makes"
    )
    assert _written(made) == [] and children.live(made) == []
    # Filed, the same machine runs.
    _filed(alpha)
    assert _run(made, at, _play(tmp_path, PING), test_connection=layer1_host(alpha)).status == 0


# ---------------------------------------------------------------- its end ----


# How long the waiting task's command sleeps, and how long a test gives what it does to end the child: a child that is
# not ended outlives the wait by far, and the test that waits fails, never passing once the sleep is over.
ASLEEP = 600
ENDS_WITHIN = 30


def _waiting(alpha: Machine) -> dict:
    """A task that says it has begun, then waits: a Ctrl-C sent then lands while nothing forks."""
    return {
        "name": "wait",
        "ansible.builtin.command": {
            "argv": ["/bin/sh", "-c", f"touch started && exec sleep {ASLEEP}"],
            "chdir": str(alpha.home),
        },
    }


async def _begun(alpha: Machine, running: asyncio.Future) -> None:
    deadline = asyncio.get_running_loop().time() + 120
    while not (alpha.home / "started").exists():
        if running.done():
            pytest.fail(f"the play ended before its task began: {running.result()}")
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail("the play's task did not begin within 120 s")
        await asyncio.sleep(0.1)


def _waiting_run(made: jobs.Job, alpha: Machine, play: Path, held: dict) -> asyncio.Future:
    return asyncio.ensure_future(
        layer1.run(
            made,
            target(alpha),
            play,
            name="alpha",
            host_flag="",
            env=dict(os.environ),
            started=lambda process: held.setdefault("process", process),
            test_connection=layer1_host(alpha),
        )
    )


async def _ended_left(held: dict, remembered: dict[int, str], running: asyncio.Future) -> None:
    """What a test left running, ended: the child's tree, and the call."""
    process = held.get("process")
    if process is not None:
        proc.end(process.pid, remembered=remembered)
    if not running.done():
        running.cancel()
        await asyncio.wait([running])


def test_sigint_to_the_child_s_group_ends_it_and_proc_end_leaves_no_process(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    play = _play(tmp_path, _waiting(alpha))

    async def scenario():
        held: dict[str, object] = {}
        remembered: dict[int, str] = {}
        running = _waiting_run(made, alpha, play, held)
        try:
            await _begun(alpha, running)
            process = held["process"]
            # Remembered before anything is signalled, as the run's ladder remembers its tree.
            remembered.update(proc.snapshot(process.pid))
            os.killpg(process.pid, signal.SIGINT)
            done, _ = await asyncio.wait([running], timeout=ENDS_WITHIN)
            assert running in done, f"SIGINT to its group did not end the child within {ENDS_WITHIN} s"
            return process, dict(remembered), running.result()
        finally:
            await _ended_left(held, remembered, running)

    process, remembered, ended = asyncio.run(scenario())
    assert ended.status != 0, ended.said
    # The tree held the task's worker and its command, a sleep among them.
    assert len(remembered) >= 2, remembered
    proc.end(process.pid, remembered=remembered)
    assert _still(remembered) == []
    assert children.live(made) == []


def test_a_call_cancelled_ends_the_child_with_its_tree(made, root, tmp_path) -> None:
    alpha = _alpha(root)
    play = _play(tmp_path, _waiting(alpha))

    async def scenario():
        held: dict[str, object] = {}
        remembered: dict[int, str] = {}
        running = _waiting_run(made, alpha, play, held)
        try:
            await _begun(alpha, running)
            process = held["process"]
            remembered.update(proc.snapshot(process.pid))
            running.cancel()
            done, _ = await asyncio.wait([running], timeout=ENDS_WITHIN)
            assert running in done, f"a cancelled call did not end its child within {ENDS_WITHIN} s"
            assert running.cancelled()
            # Before anything of the test ends what is left: what the call itself ended.
            return process, dict(remembered), _still(remembered)
        finally:
            await _ended_left(held, remembered, running)

    process, remembered, left = asyncio.run(scenario())
    # The child was ended and reaped with what ran under it, its detached worker and the task's command among them.
    assert process.returncode is not None and len(remembered) >= 2, remembered
    assert left == []
    assert children.live(made) == []


# ---------------------------------------------------------------- the connection ----


def _real_ssh() -> str:
    found = shutil.which("ssh", path=os.defpath)
    if found is None:
        pytest.skip("this machine has no ssh to ask ssh -G of")
    return found


def test_the_child_s_ssh_checks_the_key_strictly_in_the_product_s_file_over_a_host_star_that_loosens_it(
    made, root, tmp_path, engine_guard
) -> None:
    if not Path(layer1.PYTHON).is_file():
        pytest.skip(f"this machine has no {layer1.PYTHON}, the machine's interpreter the inventory names")
    alpha = _alpha(root)
    engine_guard.first_on_path(ssh_stand_in(root, [alpha]))
    at = target(alpha)
    # Ansible's own ssh connection, the product's, to the stand-in ssh: no test-only connection.
    ended = _run(made, at, _play(tmp_path, PING))
    assert ended.status == 0, ended.said
    [(_, host)] = _inventory(made, alpha)["all"]["hosts"].items()
    common = shlex.split(host["ansible_ssh_common_args"])
    assert common == runner.options(at)
    # Ansible's own ssh connection, its default: the product names no connection, and the stand-in took every call.
    assert "ansible_connection" not in host
    assert (host["ansible_python_interpreter"], host["ansible_become_exe"]) == (layer1.PYTHON, layer1.SUDO)
    first = _first(common)
    assert (first["stricthostkeychecking"], first["userknownhostsfile"], first["hostkeyalias"]) == (
        "yes",
        checks.vm_ssh_word(at.known_hosts),
        alpha.identity,
    )
    assert (first["batchmode"], first["controlmaster"]) == ("yes", "no")
    # The words Ansible gave ssh: no ssh_args of Ansible's own, so no -C, no master and no ControlPersist.
    calls = [call for call in alpha.calls() if call["outcome"] == "ran"]
    assert calls, alpha.calls()
    config = tmp_path / "loosening"
    config.write_text(LOOSENING)
    for call in calls:
        words = call["argv"][: call["argv"].index(alpha.address) + 1]
        assert "-C" not in words and not [word for word in words if word.lower().startswith("controlpersist")], words
        assert _first(words)["controlmaster"] == "no", words
        shown = subprocess.run(
            [_real_ssh(), "-G", "-F", str(config), *words],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        held: dict[str, str] = {}
        for line in shown.splitlines():
            key, _, value = line.partition(" ")
            held.setdefault(key, {"true": "yes", "false": "no"}.get(value, value))
        assert (
            held["stricthostkeychecking"],
            held["userknownhostsfile"],
            held["hostkeyalias"],
            held["batchmode"],
            held["controlmaster"],
            held["forwardagent"],
            held["checkhostip"],
            held["updatehostkeys"],
        ) == ("yes", str(at.known_hosts), alpha.identity, "yes", "no", "no", "no", "no"), shown


# ---------------------------------------------------------------- the rules with a test of their own ----


def test_the_argument_list_never_holds_key_value_after_e(tmp_path) -> None:
    play, inventory, variables = (tmp_path / name for name in ("play.yml", "inventory.yml", "vars.json"))
    for path in (play, inventory, variables):
        path.write_text("{}")
    for given in (None, variables):
        for tags in ((), ("common",), ("common", "vscode_server")):
            for check in (False, True):
                words = layer1.argv(inventory, play, given, tags, check, check)
                at = [index for index, word in enumerate(words) if word == "-e" or word.startswith("--extra")]
                assert len(at) == (given is not None), words
                assert all(words[index + 1] == f"@{variables}" for index in at), words
                assert not [word for word in words if word.startswith("-e") and word != "-e"], words
    # And in the module's own source, -e is written once, before @ and a file's path.
    found = []
    for node in ast.walk(ast.parse(LAYER1.read_text())):
        if isinstance(node, ast.List):
            for index, item in enumerate(node.elts):
                if isinstance(item, ast.Constant) and item.value == "-e":
                    after = node.elts[index + 1]
                    found.append(
                        isinstance(after, ast.JoinedStr)
                        and isinstance(after.values[0], ast.Constant)
                        and after.values[0].value == "@"
                    )
    assert found == [True]


def _passes_the_test_connection(source: str, own: bool) -> list[str]:
    """Each place source names the test-only connection: a keyword, a name, an attribute, a string, a docstring apart.
    In layer1.py itself (own) only its parameter, and that parameter handed on by its own name."""
    found = []
    tree = ast.parse(source)
    documented = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and ast.get_docstring(node, clean=False) is not None
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "test_connection":
            handed_on = isinstance(node.value, ast.Name) and node.value.id == "test_connection"
            if not (own and handed_on):
                found.append(f"line {node.value.lineno}: test_connection=")
        elif isinstance(node, ast.Name) and node.id == "test_connection" and not own:
            found.append(f"line {node.lineno}: test_connection")
        elif isinstance(node, ast.Attribute) and node.attr == "test_connection":
            found.append(f"line {node.lineno}: .test_connection")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "test_connection" in node.value:
            if id(node) in documented:
                continue
            found.append(f"line {node.lineno}: a string")
        elif isinstance(node, ast.arg) and node.arg == "test_connection" and not own:
            found.append(f"line {node.lineno}: a parameter test_connection")
    return found


def test_no_product_code_passes_the_test_only_connection() -> None:
    product = [
        path
        for path in repo_files(untracked=True)
        if (path.suffix == ".py" or path == REPO / "tools" / "aw")
        and path.relative_to(REPO).parts[0] not in ("tests", "molecule", "docs")
    ]
    assert LAYER1 in product and REPO / "tools" / "aw" in product
    found = {
        str(path.relative_to(REPO)): where
        for path in product
        if (where := _passes_the_test_connection(path.read_text(), path == LAYER1))
    }
    assert found == {}, "only a test gives layer 1 a test-only connection"
    # Its own uses are seen too, and are its parameter alone.
    assert "test_connection" in LAYER1.read_text()


@pytest.mark.parametrize(
    ("source", "own", "found"),
    [
        (
            "layer1.run(job, t, p, name='a', host_flag='', env={}, test_connection=host)",
            False,
            ["line 1: test_connection="],
        ),
        ("run(**{'test_connection': host})", False, ["line 1: a string"]),
        ("functools.partial(layer1.run, test_connection=local)", False, ["line 1: test_connection="]),
        ("given.test_connection = x", False, ["line 1: .test_connection"]),
        ("def f(test_connection=None):\n    pass", False, ["line 1: a parameter test_connection"]),
        ("host_variables(t, n, test_connection={'ansible_connection': 'local'})", True, ["line 1: test_connection="]),
        ("host_variables(t, n, test_connection=test_connection)", True, []),
        ("def f(test_connection=None):\n    return test_connection", True, []),
        ("layer1.run(job, t, p, name='a', host_flag='', env={})", False, []),
        ('def f():\n    """Passes no test_connection."""', False, []),
    ],
)
def test_the_scan_sees_each_way_the_test_only_connection_is_passed(source, own, found) -> None:
    assert _passes_the_test_connection(source, own) == found
