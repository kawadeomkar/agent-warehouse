"""The system play (playbooks/system.yml, lib/claude_vm/guest/system.py; docs/architecture/engine.md §16.10).

Its opening tasks are the guest play's of today's configure.yml, copied as they stand and held equal here, after one of
its own: the check that the connection is the one the engine wrote, which today's product makes of a VM's effective
ssh options (playbooks/tasks/host_keys.yml), made whole. Its roles are today's three system roles under today's tags,
and Tailscale's package by its role's entry system under the tag tailscale (card F14),
so that a part asked for with --tags runs that role alone (ansible-playbook's own --list-tasks says which). The check
is run here as the play has it, through layer 1's child on a stand-in machine over the test-only local connection: the
inventory the engine wrote passes it, and every source of the host's variables that outranks the inventory - a fact,
a variables file of the host's or of every host's beside the play - fails it for each variable of the connection it
sets, by name, before anything after it runs. The play's own vars and its roles' are held by the ratchet's scan
instead (test_aw_layer1_ratchet.py). Its list of variables is held to ansible-core's own: every variable of its ssh
connection and of sudo. The untrusted-template
switch layer 1 gives its child is at ignore: a module's result holding {{ is read as its text by a message, a fact, a
condition and an assert, where at error each of them fails. The check reads the inventory back by its own source,
ansible_inventory_sources, which no source of the host's variables outranks: a variables file beside the play that
points inventory_file at a forged copy saying what it sets fails it (review 45's low 3). Started as the engine starts
it, the product's child loads the play and starts it with no folder of Galaxy collections at all, the collections' path
its run's empty folder, since every module the roles call is ansible.builtin's (F6a): a module of a collection would
fail the load (F6's finding (a)). And a setting the engine cannot serve yet is refused by
name before anything is written: common_user_passwordless_sudo other than true. The play's variables hold the names it
takes alone (guest/wiring.py's NAMES): a name of the play's own facts or registers, of a role's registers or internals,
a loop's variable or a setting's row is refused by name by the play's writer, and by layer 1's hold of a variables file
given beside the play, which takes the play's names from its own list and never from its caller, before anything is
written or started; an extra variable would outrank each for every task (review 53's finding 1, review 65's 2).
"""

from __future__ import annotations

import ast
import asyncio
import base64
import importlib.util
import inspect
import json
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import yaml
from aw_machine import Machine, filed, layer1_host, machine, ssh_stand_in, target
from harness import REPO

from claude_vm.fleet import paths
from claude_vm.guest import keys, layer1, system
from claude_vm.guest import variables as play_variables
from claude_vm.runs import children, rundir
from claude_vm.runs import job as jobs
from claude_vm.ssh import hostkeys

CONFIGURE = REPO / "playbooks" / "configure.yml"
GUEST_PLAY = "Configure Claude development VMs"
CHECK = "Refuse a connection that is not the one the engine wrote"
# The guest play's opening tasks the system play copies: from the wait to root's temporary directory.
FIRST, LAST = "Wait for the VM to accept connections", "Make root's Ansible temporary directory"


def _play(path: Path) -> dict:
    (play,) = [each for each in yaml.safe_load(path.read_text()) if "hosts" in each]
    return play


def _guest_play() -> dict:
    return next(each for each in yaml.safe_load(CONFIGURE.read_text()) if each.get("name") == GUEST_PLAY)


SYSTEM = _play(system.PLAY)


@pytest.fixture
def made() -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that starts layer 1 has; both gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


def _alpha(tmp_path: Path) -> Machine:
    """A stand-in machine with a key filed under its identity, which layer 1 asks for, though the test-only local
    connection never reads it."""
    alpha = machine(tmp_path / "machines", "alpha")
    blob = (11).to_bytes(4, "big") + b"ssh-ed25519" + (32).to_bytes(4, "big") + b"\x02" * 32
    key = hostkeys.Key("ssh-ed25519", base64.b64encode(blob).decode())
    hostkeys.file(target(alpha).known_hosts, alpha.identity, key)
    return alpha


def _run(job: jobs.Job, alpha: Machine, folder: Path, play: list) -> tuple[layer1.Ended, list[jobs.Event]]:
    """play run on alpha through layer 1's child over its test-only local connection; how it ended, and its events."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "play.yml"
    path.write_text(json.dumps(play))
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    ended = asyncio.run(
        layer1.run(
            job,
            target(alpha),
            path,
            name="alpha",
            host_flag="",
            env=dict(os.environ),
            test_connection=layer1_host(alpha),
        )
    )
    return ended, heard


def _results(heard: list[jobs.Event]) -> dict[str, dict]:
    """Each task's result as its events said it, by the task's name: its status and its message."""
    return {
        str(event.data.get("task")): dict(event.data)
        for event in heard
        if event.data.get("status") in ("ok", "changed", "failed", "skipped")
    }


# ---------------------------------------------------------------------------------------------- the play itself ----


def test_the_opening_tasks_are_the_guest_play_s_own_as_they_stand() -> None:
    guest = _guest_play()["pre_tasks"]
    names = [task.get("name") for task in guest]
    copied = guest[names.index(FIRST) : names.index(LAST) + 1]
    # The check first, then the guest play's opening tasks, every key and value as configure.yml has them.
    assert SYSTEM["pre_tasks"][0]["name"] == CHECK
    assert SYSTEM["pre_tasks"][1:] == copied
    # Every one of them before the check of a golden template's options, which is the engine's own to make.
    assert names[names.index(LAST) + 1 :] == ["Check a VM cloned from a golden template against its own options"]
    assert all(task.get("tags") == ["always"] for task in SYSTEM["pre_tasks"])


def test_the_play_runs_the_three_system_roles_under_today_s_tags_then_tailscale_s_system_entry() -> None:
    assert (SYSTEM["hosts"], SYSTEM["become"], SYSTEM["gather_facts"]) == ("all", True, False)
    assert system.PARTS == ("common", "vscode_server", "dev_tools", "tailscale")
    roles = [{"role": part, "tags": [part]} for part in system.PARTS[:-1]]
    assert SYSTEM["roles"] == roles == _guest_play()["roles"][: len(roles)]
    # Tailscale's package alone, by its role's entry system, imported so that its tag reaches every task it includes;
    # never main, which today's play runs (card F14).
    assert SYSTEM["tasks"] == [
        {
            "name": "Install Tailscale where it is the VPN",
            "ansible.builtin.import_role": {"name": "tailscale", "tasks_from": "system"},
            "tags": ["tailscale"],
        }
    ]
    assert not {"post_tasks", "handlers"} & set(SYSTEM)
    assert system.PLAY == REPO.resolve() / "playbooks" / "system.yml"


class Loaded(Exception):
    """The play's start heard: the child loaded the play, and is ended there, before any task of it."""


def test_the_product_s_child_loads_the_system_play_with_no_folder_of_collections(made, tmp_path, engine_guard) -> None:
    # Started as the engine starts it: the product's own configuration (no test-only connection or folder), the run's
    # environment, the play, over Ansible's own ssh to the stand-in machine, whose own key is filed (the stand-in ssh
    # first on PATH, so that nothing reaches past it). ansible-core resolves every module of a role's statically loaded
    # tasks as the play loads; with the collections' path the run's empty folder, and the folder the caller's
    # environment names never given to the child, the play loads and starts: it needs no Galaxy collection.
    root = tmp_path / "machines"
    alpha = machine(root, "alpha")
    filed(alpha)
    engine_guard.first_on_path(ssh_stand_in(root, [alpha]))
    env = dict(os.environ)
    heard: list[jobs.Event] = []
    made.listen(heard.append)

    def loaded(event: jobs.Event) -> None:
        if event.data.get("status") == "play":
            raise Loaded(event.text)

    made.listen(loaded)
    with pytest.raises(Loaded, match="^Converge the machine's system layer$"):
        asyncio.run(layer1.run(made, target(alpha), system.PLAY, name="alpha", host_flag="", env=env))
    assert [event.data.get("status") for event in heard] == ["play"], heard
    directory = rundir.made(made)
    assert directory is not None
    configured = (directory / layer1.CONFIG).read_text().splitlines()
    assert f"collections_path = {directory / layer1.NONE}" in configured, configured
    assert children.live(made) == []


@pytest.mark.parametrize("part", system.PARTS)
def test_a_part_asked_for_runs_that_role_alone_after_the_opening_tasks(tmp_path: Path, part: str) -> None:
    # As ansible-playbook itself lists what a run with --tags would run, with the engine's own configuration and an
    # inventory of one machine as layer 1 writes them: nothing is run, nothing is reached.
    alpha = machine(tmp_path / "machines", "alpha")
    config, inventory = tmp_path / "layer1.cfg", tmp_path / "inventory.yml"
    config.write_text(layer1.configuration_text(tmp_path))
    (tmp_path / layer1.NONE).mkdir()
    inventory.write_text(json.dumps(layer1.inventory_document(target(alpha), "alpha")))
    listed = subprocess.run(
        [str(layer1.program()), "-i", str(inventory), "--list-tasks", "--tags", part, str(system.PLAY)],
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            layer1.CONFIG_VARIABLE: str(config),
            "LANG": "C.UTF-8",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert listed.returncode == 0, listed.stderr
    tasks = [line.strip().split("\tTAGS:")[0] for line in listed.stdout.splitlines() if "\tTAGS:" in line]
    # Ansible checks each role's arguments against its spec under every tag (always); of the other roles, nothing else.
    roles = {
        line.split(" : ", 1)[0]
        for line in tasks
        if " : " in line and not line.split(" : ", 1)[1].startswith("Validating arguments against arg spec")
    }
    assert roles == {part}, listed.stdout
    assert len([line for line in tasks if line.startswith(f"{part} : ")]) > 1, listed.stdout
    # The opening tasks, tagged always, still run before it: the check, and the wait inside its block.
    assert CHECK in tasks and "Wait until Ansible can run on the VM" in tasks, tasks


# --------------------------------------------------------------------------------- the check of the connection ----


def _ssh_and_sudo_variables() -> set[str]:
    """Every variable ansible-core's ssh connection and sudo read, from their own documentation, read as text."""
    package = Path(importlib.util.find_spec("ansible").origin).parent
    found = set()
    for plugin in ("connection/ssh.py", "become/sudo.py"):
        tree = ast.parse((package / "plugins" / plugin).read_text())
        (documentation,) = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DOCUMENTATION"
        ]
        for option in (yaml.safe_load(documentation).get("options") or {}).values():
            found |= {entry["name"] for entry in option.get("vars") or [] if entry["name"].startswith("ansible_")}
    return found


def test_the_check_reads_every_variable_of_the_connection_and_of_sudo(tmp_path) -> None:
    listed = SYSTEM["vars"]["system_connection_variables"]
    assert listed == sorted(set(listed))
    wanted = _ssh_and_sudo_variables() | {
        "ansible_connection",
        "ansible_python_interpreter",
        "ansible_become",
        "ansible_become_method",
    }
    assert set(listed) == wanted, (sorted(wanted - set(listed)), sorted(set(listed) - wanted))
    # Every one the inventory writes, a test-only connection's among them.
    alpha = machine(tmp_path / "machines", "alpha")
    written = {
        name
        for name in layer1.host_variables(target(alpha), "alpha", layer1_host(alpha))
        if name.startswith("ansible_") and name != "ansible_remote_tmp"
    }
    assert written <= set(listed)
    # The four review 22 named (F3.md 1) among them.
    named = {"ansible_ssh_common_args", "ansible_host_key_checking", "ansible_ssh_args", "ansible_ssh_executable"}
    assert named <= set(listed)


def _checked(
    made: jobs.Job,
    tmp_path: Path,
    before: list | None = None,
    beside: dict[str, dict | Callable[[Machine], dict]] | None = None,
) -> tuple[layer1.Ended, dict[str, dict], Machine]:
    """The play's check, as the play has it, then a task that marks the machine, run through layer 1's child on a
    stand-in; before, tasks ahead of the check, and beside, files of variables beside the play, by their paths there
    (with {identity} for the machine's), each given as written or made for the machine."""
    alpha = _alpha(tmp_path)
    for path, written in (beside or {}).items():
        where = tmp_path / "play" / path.format(identity=alpha.identity)
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(yaml.safe_dump(written(alpha) if callable(written) else written))
    check = SYSTEM["pre_tasks"][0]
    after = {"name": "after", "ansible.builtin.file": {"path": str(alpha.home / "after"), "state": "touch"}}
    play = {"hosts": "all", "gather_facts": False, "vars": SYSTEM["vars"], "pre_tasks": [*(before or []), check, after]}
    ended, heard = _run(made, alpha, tmp_path / "play", [play])
    return ended, _results(heard), alpha


def test_the_connection_the_engine_wrote_passes_the_check(made, tmp_path) -> None:
    ended, results, alpha = _checked(made, tmp_path)
    assert ended.status == 0, ended.said
    assert results[CHECK]["status"] == "ok" and results["after"]["status"] == "changed", results
    assert (alpha.home / "after").exists()


# Each variable a source outranking the inventory sets, with a value that loosens the key's checking, replaces the
# program, the account, the machine or what becomes root.
OUTRANKING = {
    "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
    "ansible_host_key_checking": False,
    "ansible_ssh_host_key_checking": False,
    "ansible_ssh_args": "-o StrictHostKeyChecking=no",
    "ansible_ssh_extra_args": "-o UserKnownHostsFile=/dev/null",
    "ansible_ssh_executable": "/bin/false",
    "ansible_ssh_host": "192.0.2.99",
    "ansible_user": "root",
    "ansible_become_exe": "/bin/sh",
    "ansible_python_interpreter": "/bin/false",
}


@pytest.mark.parametrize("name", sorted(OUTRANKING))
def test_a_fact_of_the_connection_fails_the_check_by_its_name_before_anything_runs(made, tmp_path, name) -> None:
    fact = {"name": "set", "ansible.builtin.set_fact": {name: OUTRANKING[name]}}
    ended, results, alpha = _checked(made, tmp_path, before=[fact])
    assert ended.status != 0
    assert results[CHECK]["status"] == "failed", results
    said = results[CHECK]["message"]
    assert said.startswith(f"{alpha.identity}: {name} set where it outranks the inventory the engine wrote"), said
    assert "after" not in results and not (alpha.home / "after").exists()


def test_a_variables_file_of_the_host_s_or_every_host_s_beside_the_play_fails_the_check(made, tmp_path) -> None:
    beside = {
        "group_vars/all.yml": {"ansible_host_key_checking": False},
        "host_vars/{identity}.yml": {"ansible_ssh_args": "-o StrictHostKeyChecking=no"},
    }
    ended, results, alpha = _checked(made, tmp_path, beside=beside)
    assert ended.status != 0
    said = results[CHECK]["message"]
    assert said.startswith(f"{alpha.identity}: ansible_host_key_checking, ansible_ssh_args set where it outranks"), said


def test_a_variables_file_that_points_inventory_file_at_a_forged_copy_fails_the_check(made, tmp_path) -> None:
    # Review 45's low 3: inventory_file is a host variable, which a source the check guards against can set too. A copy
    # of the inventory that says what the file beside the play says would pass a check that read the inventory back by
    # that name; the check reads its own source, ansible_inventory_sources, which no source of a host's variables sets.
    loosened = OUTRANKING["ansible_ssh_common_args"]
    forged = tmp_path / "forged.json"

    def host_vars(alpha: Machine) -> dict:
        document = layer1.inventory_document(target(alpha), "alpha", layer1_host(alpha))
        document["all"]["hosts"][alpha.identity]["ansible_ssh_common_args"] = loosened
        forged.write_text(json.dumps(document))
        return {"ansible_ssh_common_args": loosened, "inventory_file": str(forged)}

    ended, results, alpha = _checked(made, tmp_path, beside={"host_vars/{identity}.yml": host_vars})
    assert forged.is_file() and ended.status != 0
    said = results[CHECK]["message"]
    assert said.startswith(f"{alpha.identity}: ansible_ssh_common_args set where it outranks"), said
    assert "after" not in results and not (alpha.home / "after").exists()
    assert "ansible_inventory_sources | first" in SYSTEM["pre_tasks"][0]["vars"]["system_inventory"]


def test_a_variable_of_the_connection_the_play_or_a_role_sets_is_held_by_the_ratchet_s_scan() -> None:
    # The check reads the host's variables, and the play's own vars and its roles' are none of them: the play sets
    # only the check's list, and the ratchet's scan holds every file the play reaches to setting no ansible_ variable.
    import test_aw_layer1_ratchet as ratchet

    assert list(SYSTEM["vars"]) == ["system_connection_variables"]
    assert ratchet.setting_connection_variables() == []


# ------------------------------------------------------------------------------- the untrusted-template switch ----


def _reads_a_template_a_machine_printed(made, tmp_path) -> dict[str, dict]:
    """A module's result holding {{ 7 * 7 }} read by a message, a fact, a condition and an assert, each going on past
    its own failure, run through layer 1's child; each task's result by its name."""
    alpha = _alpha(tmp_path)
    tasks = [
        {
            "name": "print",
            "ansible.builtin.command": {"argv": ["printf", r"\173\173 7 * 7 }}"]},
            "register": "out",
            "changed_when": False,
        },
        {"name": "message", "ansible.builtin.debug": {"msg": "{{ out.stdout }}"}, "ignore_errors": True},
        {"name": "fact", "ansible.builtin.set_fact": {"seen": "{{ out.stdout }}"}, "ignore_errors": True},
        {
            "name": "condition",
            "ansible.builtin.debug": {"msg": "reached"},
            "when": "out.stdout | length > 0",
            "ignore_errors": True,
        },
        {
            "name": "assertion",
            "ansible.builtin.assert": {"that": "out.stdout is search('7 [*] 7')", "quiet": True},
            "ignore_errors": True,
        },
    ]
    _, heard = _run(made, alpha, tmp_path / "play", [{"hosts": "all", "gather_facts": False, "tasks": tasks}])
    return _results(heard)


def test_a_machine_s_output_holding_a_template_is_read_as_its_text(made, tmp_path) -> None:
    assert layer1.UNTRUSTED == "ignore"
    results = _reads_a_template_a_machine_printed(made, tmp_path)
    assert {name: results[name]["status"] for name in ("message", "fact", "condition", "assertion")} == dict.fromkeys(
        ("message", "fact", "condition", "assertion"), "ok"
    ), results
    # As its text, never rendered.
    assert results["message"]["message"] == "{{ 7 * 7 }}", results["message"]
    assert results["condition"]["message"] == "reached"


def test_at_error_each_read_of_it_fails_its_task(made, tmp_path, monkeypatch) -> None:
    # The control: the switch at error, as F3 first set it, fails each of the four.
    monkeypatch.setattr(layer1, "UNTRUSTED", "error")
    results = _reads_a_template_a_machine_printed(made, tmp_path)
    for name in ("message", "fact", "condition", "assertion"):
        assert results[name]["status"] == "failed", (name, results[name])
        assert "untrusted template" in str(results[name].get("message", "")).lower(), results[name]


# ---------------------------------------------------------------------------- what the engine cannot serve yet ----


@pytest.mark.parametrize("value", [False, "false", 0, 1, None])
def test_passwordless_sudo_turned_off_is_refused_by_name_before_anything_is_written(made, value) -> None:
    path = rundir.path_for(made, "vars.json")
    with pytest.raises(system.Refused, match="^common_user_passwordless_sudo is served as true alone for now: "):
        system.variables(path, {"common_user_passwordless_sudo": value, "common_user": "dev"}, secrets=())
    assert not path.exists()
    # Said as the variables file's own refusal, which a caller of layer 1 already reads.
    assert issubclass(system.Refused, play_variables.Refused)


@pytest.mark.parametrize("document", [{"common_user_passwordless_sudo": True}, {"common_user": "dev"}, {}])
def test_what_the_engine_serves_is_written_by_the_variables_writer(made, document) -> None:
    path = system.variables(rundir.path_for(made, "vars.json"), document, secrets=())
    assert path == rundir.path_for(made, "vars.json")
    assert play_variables.held(path, system.NAMES) == path
    assert json.loads(path.read_text()) == play_variables.wrap(document)


# ------------------------------------------------------------------------------- the names the play takes alone ----

# Names of a role variable's shape that the system play's variables never hold, and an extra variable would outrank for
# every task: the play's own facts and registers (its check that the connection is the engine's, the guest play's
# base), a role's registers and internals (common's list of files moved as root, its sshd settings, the password's
# salt), a loop's variable, a setting's row, and a role variable of another play's.
NOT_TAKEN = [
    "system_not_written",
    "system_connection_variables",
    "configure_base",
    "configure_no_connection",
    "common_names_1x",
    "common_sshd_hardening",
    "common_user_password_salt",
    "item",
    "vm_user",
    "claude_code_version",
]


def _layer1_holds(job: jobs.Job, alpha: Machine, given: Path) -> layer1.Refused:
    """What layer 1 says of the variables file given beside the system play, with an environment that is no mapping: a
    file it holds is refused for that, the check held after the file's, so that nothing is written or started either
    way."""
    with pytest.raises(layer1.Refused) as refused:
        asyncio.run(layer1.run(job, target(alpha), system.PLAY, given, name="alpha", host_flag="", env="a word"))
    directory = rundir.made(job)
    assert directory is not None and list(directory.iterdir()) == [given] * (given.parent == directory)
    assert children.live(job) == []
    return refused.value


@pytest.mark.parametrize("name", NOT_TAKEN)
def test_a_name_the_play_does_not_take_is_refused_by_its_writer_and_by_layer_1_s_hold(made, tmp_path, name) -> None:
    said = f"{name} is no variable of the play's: its variables hold the names it takes alone"
    assert name not in system.NAMES
    path = rundir.path_for(made, "vars.json")
    # The writer's own refusal, which a caller of layer 1 already reads.
    with pytest.raises(play_variables.Refused) as refused:
        system.variables(path, {"common_user": "dev", name: True}, secrets=())
    assert str(refused.value).startswith(said), refused.value
    assert not path.exists()
    # A file made by hand, as the writer would have made it but for the name, given beside the play.
    given = tmp_path / "given.json"
    given.write_text(json.dumps({"common_user": {"__ansible_unsafe": "dev"}, name: True}))
    assert str(_layer1_holds(made, _alpha(tmp_path), given)).startswith(said)


def test_layer_1_s_hold_takes_what_the_play_s_writer_writes(made, tmp_path) -> None:
    written = system.variables(rundir.path_for(made, "vars.json"), {"common_user": "dev"}, secrets=())
    said = str(_layer1_holds(made, _alpha(tmp_path), written))
    assert said.startswith("the run's environment is a mapping of names to values"), said
    assert layer1.PLAY_NAMES == {system.PLAY: system.NAMES, keys.PLAY: keys.NAMES}


# Lists a caller might hand layer 1 for a file that names two of the play's own (its check's fact, the base's register):
# the file's own names, a set made from them, and the play's list with the two added (review 65's finding 2).
GIVEN_NAMES = {
    "the file's names": lambda document: list(document),
    "a set made from them": lambda document: set(document),
    "the play's names and the two": lambda document: system.NAMES | {"system_not_written", "configure_base"},
}


@pytest.mark.parametrize("case", list(GIVEN_NAMES))
def test_a_caller_s_own_list_of_names_is_refused_and_the_play_s_own_holds_the_file(made, tmp_path, case) -> None:
    alpha = _alpha(tmp_path)
    document = {"common_user": "dev", "system_not_written": [], "configure_base": {"stdout": ""}}
    given = tmp_path / "given.json"
    given.write_text(json.dumps(play_variables.wrap(document)))
    # layer1.run takes no list of names at all: a caller's is refused before anything is held, written or started.
    assert "names" not in inspect.signature(layer1.run).parameters
    with pytest.raises(TypeError, match="names"):
        asyncio.run(
            layer1.run(
                made,
                target(alpha),
                system.PLAY,
                given,
                name="alpha",
                host_flag="",
                env={},
                names=GIVEN_NAMES[case](document),
            )
        )
    assert rundir.made(made) is None or list(rundir.made(made).iterdir()) == []
    assert children.live(made) == []
    # Without it, the file is held to the play's own list, which takes neither.
    said = "system_not_written is no variable of the play's: its variables hold the names it takes alone"
    assert str(_layer1_holds(made, alpha, given)).startswith(said)
