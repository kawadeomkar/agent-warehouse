"""The stand-in machines of the new engine's tests (tests/unit/aw_machine.py; docs/architecture/engine.md §16.10).

Every machine here is a directory of the test's own, and every connection to one goes through B3's runner to the
stand-in ssh, first on the test's own PATH by engine_guard.first_on_path. A command lands in the machine whose address
it names and in no other, and changes nothing outside that machine's directory; an address no machine has comes back as
the runner's Unreachable; words whose first StrictHostKeyChecking or BatchMode is not yes, another account and a sudo
named by its path are refused, and nothing runs; standard input arrives whole, and a held child reads a line sent
later; a machine's command has its own HOME, its bin/ first on PATH and the test's own failing stand-ins next, and
nothing else of the test's environment; a sudo that asks receives the password only after -n was refused for want of
one, and once, kept nowhere but as a digest; -O check and -O stop answer as a live master would, and a forward is
recorded and never opened. A machine's home and system files are made as today's stand-in VMs make theirs
(tests/unit/test_status_vms.py's `_vms`, case by case); planted makes the Proxmox fake's VM report the machine's address
and identity; and a play of one task over the local connection touches only its machine's directory. The rule with a
test of its own: no test_aw_*.py file writes an ssh stand-in of its own, but the files that test the ssh layer itself,
each named with why.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import harness
import pytest
from aw_kit import searched_for
from aw_machine import (
    CONTROL,
    Machine,
    layer1_host,
    machine,
    planted,
    ssh_stand_in,
    target,
    unrouted,
)
from harness import REPO, _pve, ansible_config, fake_fleet, repo_files
from test_status_vms import CASES, _vms

from claude_vm import checks, proxmox
from claude_vm.fleet import paths
from claude_vm.pve import agent, answers
from claude_vm.runs import children
from claude_vm.runs import job as jobs
from claude_vm.secret import Secret
from claude_vm.ssh import runner

UNIT = REPO / "tests" / "unit"
# A sudo password no file, word or variable may hold: low in entropy, so that gitleaks takes it for no key.
PASSWORD = "unit-test-aw-machine-sudo-canary"
# What each machine's command is asked for, where it meets a program: the stand-ins a run must never reach past.
DESKTOP = ("open", "xdg-open", "pbcopy")
REFUSED = ("ssh", "security", "ansible-playbook")
OWN = ("gh", "claude", "tailscale", "systemctl", "dpkg-query", "getent", "sudo")


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own, as the run that starts the children has; dropped after the test."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    yield job
    jobs.drop(job)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "machines"


@pytest.fixture
def two(root: Path, engine_guard) -> tuple[Machine, Machine, Path]:
    """alpha, ready as status_vms' ready VM is, and beta, as a machine is made by default; the stand-in ssh's folder,
    first on this test's own PATH."""
    alpha = machine(root, "alpha", **CASES["ready"])
    beta = machine(root, "beta")
    folder = ssh_stand_in(root, [alpha, beta])
    engine_guard.first_on_path(folder)
    return alpha, beta, folder


def _run(job: jobs.Job, at: runner.Target, command: list[str], stdin=None) -> runner.Result:
    return asyncio.run(runner.run(job, at, command, stdin, runner.LIMITS, dict(os.environ)))


def _stand_in(folder: Path, words: list[str]) -> subprocess.CompletedProcess[str]:
    """The stand-in ssh in folder run by its path with words, as a run's ssh is given them."""
    return subprocess.run(
        [str(folder / "ssh"), *words], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
    )


def _snapshot(top: Path) -> dict[str, object]:
    """Every entry under top by its path from top: a folder, a link and where it points, or a file's bytes and mode. A
    git checkout's own .git is said to be there and no more: its objects carry the time they were made."""
    seen: dict[str, object] = {}
    for where, folders, files in os.walk(top):
        here = Path(where)
        for name in [*folders, *files]:
            path = here / name
            relative = str(path.relative_to(top))
            if path.is_symlink():
                seen[relative] = ("link", os.readlink(path))
            elif path.is_dir():
                seen[relative] = "folder"
            else:
                seen[relative] = ("file", path.read_bytes(), path.stat().st_mode)
        # os.walk follows no link; a checkout's .git is said and not read.
        folders[:] = [name for name in folders if name != ".git"]
    return seen


def _changed(before: dict, after: dict) -> list[str]:
    return sorted(path for path in {*before, *after} if before.get(path) != after.get(path))


# ---------------------------------------------------------------- the runner reaches a machine ----


def test_a_command_run_through_the_runner_lands_in_its_machine_and_in_no_other(two, made) -> None:
    alpha, beta, folder = two
    command = ["sh", "-c", 'pwd; echo "$HOME"; touch landed']
    ran = _run(made, target(alpha), command)
    assert isinstance(ran, runner.Done) and (ran.status, ran.said) == (0, ""), ran
    assert [Path(line).resolve() for line in ran.lines] == [alpha.home.resolve()] * 2
    assert (alpha.home / "landed").is_file() and not (beta.home / "landed").exists()
    # The call is the machine's alone: the runner's words as they were given, and the job's marker among the names of
    # the variables it was started with.
    [call] = alpha.calls()
    assert call["argv"] == runner.argv(target(alpha), command)[1:] and call["outcome"] == "ran"
    assert children.MARKER in call["env"]
    assert beta.calls() == [] and unrouted(folder) == []


def test_after_a_run_only_the_machine_s_own_directory_has_changed(two, made, tmp_path) -> None:
    alpha, *_ = two
    before = _snapshot(tmp_path)
    _run(made, target(alpha), ["sh", "-c", 'echo kept > "$HOME/.claude/note"; mkdir made; touch made/one'])
    _run(made, target(alpha), ["rm", "-f", "dpkg-status"])
    changed = _changed(before, _snapshot(tmp_path))
    # The test's own home holds the run's job, which is the run's and no machine's.
    changed = [path for path in changed if not path.startswith("aw-home/")]
    assert {"machines/alpha/calls.log", "machines/alpha/home/.claude/note", "machines/alpha/home/made/one"} <= set(
        changed
    ), changed
    assert "machines/alpha/home/dpkg-status" in changed
    assert [path for path in changed if not path.startswith("machines/alpha/")] == []


def test_an_address_no_machine_has_comes_back_as_the_runner_s_unreachable(two, made, tmp_path) -> None:
    alpha, beta, folder = two
    stranger = runner.machine(alpha.identity, "203.0.113.250", alpha.account, CONTROL, known_hosts=tmp_path / "kh")
    got = _run(made, stranger, ["touch", "landed"])
    assert isinstance(got, runner.Unreachable) and not got.heard and got.lines == (), got
    assert "203.0.113.250 port 22: No route to host" in got.said
    # ssh -G, which reads the person's own configuration, is never answered: this computer's own ssh is asked that.
    shown = _stand_in(folder, ["-G", "-F", "/dev/null", alpha.address])
    assert shown.returncode == 255 and "answers no -G" in shown.stderr
    assert [call["outcome"] for call in unrouted(folder)] == ["refused: no machine has the address", "refused: -G"]
    assert alpha.calls() == beta.calls() == []
    assert not (alpha.home / "landed").exists() and not (beta.home / "landed").exists()


def _without(words: list[str], option: str) -> list[str]:
    at = words.index(option)
    assert words[at - 1] == "-o"
    return words[: at - 1] + words[at + 1 :]


def test_words_that_take_a_key_on_first_use_ask_or_name_another_account_are_refused_and_nothing_runs(
    two, made, tmp_path
) -> None:
    alpha, _, folder = two
    # A first use takes the key it is shown (accept-new): the stand-in, which shows none, refuses it.
    first = target(alpha, first_use=True, known_hosts=tmp_path / "scratch")
    got = _run(made, first, ["touch", "landed"])
    assert isinstance(got, runner.Unreachable) and "StrictHostKeyChecking=yes and BatchMode=yes" in got.said, got
    words = runner.argv(target(alpha), ["touch", "landed"])[1:]
    # The runner always gives BatchMode=yes: these words are made here without it.
    no_batch = _stand_in(folder, _without(words, "BatchMode=yes"))
    # ssh keeps the first value given: a yes after a no makes up for nothing.
    earlier = _stand_in(folder, ["-o", "StrictHostKeyChecking=no", *words])
    # The account the machine was made with is the one it takes: given by -l, or by -o User=, as Ansible's ssh
    # connection gives it, quoted, before the runner's own -l.
    other = _stand_in(folder, ["-l", "root", *words])
    named = _stand_in(folder, ["-o", 'User="root"', *words])
    for refused in (no_batch, earlier):
        assert refused.returncode == 255 and "StrictHostKeyChecking=yes and BatchMode=yes" in refused.stderr
    for refused in (other, named):
        assert refused.returncode == 255 and f"root@{alpha.address}: Permission denied (publickey)." in refused.stderr
    assert not (alpha.home / "landed").exists()
    assert [call["outcome"] for call in alpha.calls()] == [
        "refused: not StrictHostKeyChecking=yes and BatchMode=yes",
        "refused: not StrictHostKeyChecking=yes and BatchMode=yes",
        "refused: not StrictHostKeyChecking=yes and BatchMode=yes",
        "refused: another account",
        "refused: another account",
    ]


def test_a_sudo_named_by_its_path_is_refused_as_this_computer_s_own(two, made) -> None:
    alpha, *_ = two
    for command in (["/usr/bin/sudo", "-n", "true"], ["sh", "-c", "exec /bin/sudo -n true"]):
        got = _run(made, target(alpha), command)
        assert isinstance(got, runner.Unreachable) and "names a sudo by its path" in got.said, (command, got)
    assert alpha.sudo_calls() == []
    assert [call["outcome"] for call in alpha.calls()] == ["refused: this computer's own sudo"] * 2


def test_standard_input_arrives_whole_and_a_held_child_reads_a_line_sent_later(two, made) -> None:
    alpha, *_ = two
    # Every byte, NUL and newline among them, and an end with no newline.
    data = bytes(range(256)) * 4096 + b"the end, with no newline"
    got = _run(made, target(alpha), ["sh", "-c", 'cat > "$HOME/received"'], data)
    assert got == runner.Done(0, (), "")
    assert (alpha.home / "received").read_bytes() == data

    async def held() -> tuple[str, runner.Result]:
        command = ["sh", "-c", 'echo ready; read line; echo "got $line"']
        child = await runner.hold(made, target(alpha), command, dict(os.environ), deadline=30)
        async for line in child.lines():
            first = line
            break
        await child.send("a line sent once the child spoke")
        return first, await child.result()

    assert asyncio.run(held()) == ("ready", runner.Done(0, ("ready", "got a line sent once the child spoke"), ""))


def test_a_machine_s_command_has_its_own_environment_and_meets_the_stand_ins_first(two, made, engine_guard) -> None:
    alpha, *_ = two
    tools = " ".join([*DESKTOP, *REFUSED, *OWN])
    command = ["sh", "-c", f'env; echo "-- found"; for tool in {tools}; do command -v "$tool"; done']
    got = _run(made, target(alpha), command)
    assert isinstance(got, runner.Done) and got.status == 0, got
    at = got.lines.index("-- found")
    variables = dict(line.split("=", 1) for line in got.lines[:at] if "=" in line)
    # What a shell, or macOS for each process, sets for itself aside: the machine's HOME, its PATH and today's
    # stand-ins' STAND_IN alone.
    itself = {"PWD", "OLDPWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING"}
    assert set(variables) - itself == {"HOME", "PATH", "STAND_IN"}, sorted(variables)
    assert variables["HOME"] == variables["STAND_IN"] == str(alpha.home)
    assert variables["PATH"].split(os.pathsep)[0] == str(alpha.bin)
    found = dict(zip([*DESKTOP, *REFUSED, *OWN], got.lines[at + 1 :], strict=True))
    standing = engine_guard.stand_ins
    assert {tool: found[tool] for tool in DESKTOP} == {tool: str(standing / ".desktop" / tool) for tool in DESKTOP}
    assert {tool: found[tool] for tool in REFUSED} == {tool: str(standing / ".refused" / tool) for tool in REFUSED}
    assert {tool: found[tool] for tool in OWN} == {tool: str(alpha.bin / tool) for tool in OWN}


# ---------------------------------------------------------------- sudo ----


def test_a_sudo_that_asks_gets_the_password_only_after_n_was_refused_and_once(root, made, engine_guard) -> None:
    asking = machine(root, "asking", sudo_password=PASSWORD)
    engine_guard.first_on_path(ssh_stand_in(root, [asking]))
    at = target(asking)
    refused = _run(made, at, ["sudo", "-n", "-H", "true"])
    assert refused == runner.Done(1, (), "sudo: a password is required")
    # The password's line, then what the command reads: sudo takes the one line and leaves the rest.
    sent = [Secret(PASSWORD), "\nrest"]
    took = _run(made, at, ["sudo", "-S", "-p", "", "-H", "sh", "-c", 'cat > "$HOME/rest"'], sent)
    assert took == runner.Done(0, (), "")
    assert (asking.home / "rest").read_text() == "rest"
    # A wrong password is one line read and refused: the line after it is never tried as a second.
    wrong = _run(made, at, ["sudo", "-S", "-p", "", "sh", "-c", 'cat > "$HOME/after"'], f"wrong\n{PASSWORD}\n")
    assert isinstance(wrong, runner.Done) and wrong.status == 1 and "1 incorrect password attempt" in wrong.said
    assert not (asking.home / "after").exists()
    assert [(call["n"], call["S"], call["read"], call["outcome"]) for call in asking.sudo_calls()] == [
        (True, False, 0, "refused: a password is required"),
        (False, True, 1, "ran"),
        (False, True, 1, "refused: a wrong password"),
    ]
    # Nowhere on disk: no word, no variable, no record holds it, and the machine keeps its digest alone.
    assert searched_for([PASSWORD], root) == []


def test_a_sudo_that_asks_nothing_reads_nothing_of_standard_input(root, made, engine_guard) -> None:
    free = machine(root, "free")
    engine_guard.first_on_path(ssh_stand_in(root, [free]))
    assert _run(made, target(free), ["sudo", "-n", "true"]) == runner.Done(0, (), "")
    # As sudo with no password to ask: -S reads no line, and the command is given the whole of standard input.
    took = _run(made, target(free), ["sudo", "-S", "-p", "", "sh", "-c", 'cat > "$HOME/all"'], "line\nrest")
    assert took == runner.Done(0, (), "") and (free.home / "all").read_text() == "line\nrest"
    assert [(call["n"], call["S"], call["read"], call["outcome"]) for call in free.sudo_calls()] == [
        (True, False, 0, "ran"),
        (False, True, 0, "ran"),
    ]


# ---------------------------------------------------------------- a master and a forward ----


def test_a_master_answers_check_and_stop_and_a_forward_is_recorded_and_never_opened(two, made) -> None:
    alpha, _, folder = two
    at = target(alpha)
    options, place = runner.options(at), ["-l", at.account, "-p", str(at.port), "--", at.address]
    check = _stand_in(folder, [*options, "-O", "check", *place])
    stop = _stand_in(folder, [*options, "-O", "stop", *place])
    gone = _stand_in(folder, [*options, "-O", "exit", *place])
    assert check.returncode == 0 and check.stderr.startswith("Master running (pid=")
    assert stop.returncode == 0 and stop.stderr.startswith("Stop listening request sent.")
    assert gone.returncode == 255 and "-O exit is not answered" in gone.stderr
    port = harness._free_port()
    spec = f"127.0.0.1:{port}:127.0.0.1:{port}"
    # In the background at once (-f), as ssh goes once its forwards are bound: none ever is.
    background = _stand_in(folder, [*runner.options(at, forward=True), "-f", "-L", spec, *place, "sleep", "1"])
    assert background.returncode == 0

    async def forwarded() -> list[str]:
        held = await runner.forward(made, at, [spec], dict(os.environ), deadline=30)
        try:
            for _ in range(200):
                if len(alpha.calls()) == 5:
                    break
                await asyncio.sleep(0.05)
            with pytest.raises(ConnectionRefusedError):
                socket.create_connection(("127.0.0.1", port), timeout=5).close()
            return [call["outcome"] for call in alpha.calls()]
        finally:
            await held.end()

    assert asyncio.run(forwarded()) == [
        "-O check",
        "-O stop",
        "refused: -O exit",
        "forward, never opened",
        "forward, never opened",
    ]


# ---------------------------------------------------------------- what a machine holds ----


def _link(target_of: str, top: Path) -> object:
    """A link as a machine's own: where it points within top, or which program of the stand-ins' it is."""
    pointed = Path(target_of)
    return ("in", str(pointed.relative_to(top))) if pointed.is_relative_to(top) else ("program", pointed.name)


def _held_as_a_machine(top: Path, system: Path | None = None) -> dict[str, object]:
    """top's entries as _snapshot gives them, each link read by _link, with system's under system/ where it is a folder
    of its own."""
    seen = {}
    for path, entry in _snapshot(top).items():
        seen[path] = _link(entry[1], top) if isinstance(entry, tuple) and entry[0] == "link" else entry
    if system is not None:
        seen.update({f"system/{path}": entry for path, entry in _snapshot(system).items()})
        seen["system"] = "folder"
    return seen


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_machine_s_home_and_system_files_are_made_as_today_s_stand_in_vms_make_them(tmp_path, case) -> None:
    _vms(tmp_path / "today", {case: CASES[case]})
    made = machine(tmp_path / "machines", case, **CASES[case])
    assert _held_as_a_machine(made.home, made.system) == _held_as_a_machine(tmp_path / "today" / case)


def test_each_machine_is_given_an_address_of_rfc_5737_s_ranges_and_an_identity_of_its_own(root) -> None:
    made = [machine(root, f"m{index}") for index in range(3)]
    assert [one.address for one in made] == ["203.0.113.10", "203.0.113.11", "203.0.113.12"]
    assert len({one.identity for one in made}) == 3
    assert all(checks.HOST_KEY_ALIAS_RE.fullmatch(one.identity) for one in made)
    assert machine(root, "given", address="198.51.100.7").address == "198.51.100.7"
    for name, address in (("private", "10.0.0.5"), ("public", "8.8.8.8"), ("taken", "203.0.113.10"), ("odd", "x")):
        with pytest.raises(pytest.fail.Exception):
            machine(root, name, address=address)
        assert not (root / name).exists()


# ---------------------------------------------------------------- the fake reports a machine ----


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[str]:
    yield from fake_fleet(tmp_path)


def _reported(fake: str, vmid: int) -> tuple[answers.VmConfig, str | None]:
    """vmid's configuration as the engine reads it, and the address its agent reports on the VM's own card."""
    config = answers.config(_pve(fake, "GET", f"nodes/pve/qemu/{vmid}/config")["data"], "pve", vmid, "net0")
    cards = _pve(fake, "GET", f"nodes/pve/qemu/{vmid}/agent/network-get-interfaces")["data"]["result"]
    return config, agent.address(cards, config.mac)


def test_planted_makes_the_fake_s_vm_report_the_machine_s_address_and_identity(fake, root) -> None:
    alpha = machine(root, "alpha")
    before, _ = _reported(fake, 9010)
    planted(fake, 9010, alpha)
    config, address = _reported(fake, 9010)
    assert (config.identity, address, config.mac) == (alpha.uuid, alpha.address, before.mac)
    assert proxmox.ssh_host_key_alias(dict(config.values)) == alpha.identity
    # Another VM keeps its own identity and the address the fake's agent gives it.
    other, theirs = _reported(fake, 9011)
    assert other.identity not in ("", alpha.uuid) and theirs == "192.0.2.61"


# ---------------------------------------------------------------- layer 1 on a machine ----


def test_a_play_of_one_task_over_the_local_connection_touches_only_its_machine_s_directory(root, tmp_path) -> None:
    alpha, beta = machine(root, "alpha"), machine(root, "beta")
    host = layer1_host(alpha)
    assert (host["ansible_connection"], host["ansible_become"], host["ansible_become_exe"]) == (
        "local",
        False,
        str(alpha.bin / "sudo"),
    )
    # Its interpreter is today's wrapper, run from the machine's home: that HOME, and the machine's bin/ first.
    asked = "import os, shutil; print(os.environ['HOME'], shutil.which('gh'))"
    shown = subprocess.run(
        [str(host["ansible_python_interpreter"]), "-c", asked],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert shown.stdout.split() == [str(alpha.home), str(alpha.bin / "gh")]
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(json.dumps({"all": {"hosts": {alpha.identity: host}}}))
    play = tmp_path / "play.yml"
    task = {"name": "made", "ansible.builtin.file": {"path": "~/made-by-a-play", "state": "touch"}}
    play.write_text(json.dumps([{"hosts": "all", "gather_facts": False, "tasks": [task]}]))
    before = _snapshot(root)
    ran = subprocess.run(
        [str(harness.ANSIBLE_PLAYBOOK), "-i", str(inventory), str(play)],
        cwd=tmp_path,
        env={**os.environ, "ANSIBLE_CONFIG": ansible_config(tmp_path)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        process_group=0,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (alpha.home / "made-by-a-play").is_file() and not (beta.home / "made-by-a-play").exists()
    changed = _changed(before, _snapshot(root))
    assert "alpha/home/made-by-a-play" in changed and [path for path in changed if not path.startswith("alpha/")] == []
    # The module's own files went to the play's remote folder, in the machine's directory, and were taken away.
    assert "alpha/ansible-tmp" in changed and list((alpha.directory / "ansible-tmp").iterdir()) == []


# ---------------------------------------------------------------- one stand-in ssh ----

# The engine's test files that write an ssh stand-in of their own, each with why: what they test is the ssh layer, or
# the guard, itself. Every other reaches a machine through aw_machine's.
OWN_SSH: dict[str, str] = {
    "test_aw_ssh_runner.py": "the runner's own tests: a stand-in that records every word, variable and byte the runner"
    " gives it, and plays each way a child ends, is what they hold the command line and its bounds to",
    "test_aw_ssh_connections.py": "the shared connections' own tests: the masters, -O check's answers and the socket"
    " files they leave are what they hold",
    "test_aw_children.py": "a stand-in that says the marker it was handed, for route.ssh_shown's: what a child"
    " carries, not a machine",
    "test_aw_guards.py": "the guard's own tests of first_on_path: a program of the test's own ahead of the stand-ins",
}
# What writes a file: a method of the path written, or a call given the path.
WRITTEN_BY = {"write_text", "write_bytes", "symlink_to", "hardlink_to", "touch"}
WRITTEN_TO = {"_executable", "copy", "copy2", "copyfile", "symlink", "link"}


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _ssh_bound(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names bound to the word ssh - by an assignment, or as a loop's over a literal that holds it - and those bound
    to a path that ends in it."""
    words: set[str] = set()
    paths: set[str] = set()
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
                items = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)) else []
                if any(_ssh_word(item, words) for item in items):
                    words.add(node.target.id)
            if isinstance(node, ast.Assign):
                names = {target.id for target in node.targets if isinstance(target, ast.Name)}
                if _ssh_word(node.value, words):
                    words |= names
                elif _ssh_path(node.value, words, paths):
                    paths |= names
    return words, paths


def _ssh_word(node: ast.AST, words: set[str]) -> bool:
    return (isinstance(node, ast.Constant) and node.value == "ssh") or (isinstance(node, ast.Name) and node.id in words)


def _ssh_path(node: ast.AST, words: set[str], paths: set[str]) -> bool:
    """Whether node is a path whose last part is ssh: joined by /, by os.path.join or joinpath, spelled whole, or a
    name bound to one."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _ssh_word(node.right, words)
    if isinstance(node, ast.Call) and _callee(node) in ("join", "joinpath", "Path") and node.args:
        return _ssh_word(node.args[-1], words) or _ssh_path(node.args[-1], words, paths)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith("/ssh")
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        return isinstance(last, ast.Constant) and str(last.value).endswith("/ssh")
    return isinstance(node, ast.Name) and node.id in paths


def _writes_ssh(source: str) -> list[int]:
    """The lines of source that write a program named ssh: a stand-in of the file's own."""
    tree = ast.parse(source)
    words, paths = _ssh_bound(tree)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name in WRITTEN_BY and isinstance(node.func, ast.Attribute):
            if _ssh_path(node.func.value, words, paths):
                found.add(node.lineno)
        elif name in WRITTEN_TO and any(_ssh_path(arg, words, paths) for arg in node.args):
            found.add(node.lineno)
        elif name == "open" and node.args and _ssh_path(node.args[0], words, paths):
            given = [keyword.value for keyword in node.keywords if keyword.arg == "mode"]
            mode = node.args[1] if len(node.args) > 1 else next(iter(given), None)
            if isinstance(mode, ast.Constant) and set(str(mode.value)) & set("wax+"):
                found.add(node.lineno)
    return sorted(found)


def test_no_engine_test_writes_an_ssh_stand_in_of_its_own() -> None:
    files = [path for path in repo_files("tests/unit", untracked=True) if path.name.startswith("test_aw_")]
    assert UNIT / "test_aw_machine.py" in files and UNIT / "test_aw_ssh_runner.py" in files, len(files)
    found = {path.name: lines for path in files if path.suffix == ".py" and (lines := _writes_ssh(path.read_text()))}
    assert sorted(found) == sorted(OWN_SSH), (
        "reach a machine through tests/unit/aw_machine.py's ssh_stand_in, or name a file that tests the ssh layer"
        f" itself in OWN_SSH with why: {found}"
    )
    assert all(reason.strip() for reason in OWN_SSH.values())


@pytest.mark.parametrize(
    ("source", "writes"),
    [
        ('(folder / "ssh").write_text("#!/bin/sh\\n")', True),
        ('harness._executable(folder / "ssh", STAND_IN)', True),
        ('_executable(tmp_path / "bin" / "ssh", "x")', True),
        ('ssh = folder / "ssh"\nssh.write_text("x")', True),
        ('for name in ("ps", "ssh"):\n    (folder / name).write_text("x")', True),
        ('os.symlink(real, os.path.join(folder, "ssh"))', True),
        ('shutil.copy(stand_in, folder / "ssh")', True),
        ('open(folder / "ssh", "w").write("x")', True),
        ('Path(f"{folder}/ssh").write_text("x")', True),
        ('(folder / "ssh").symlink_to(real)', True),
        ('(tmp_path / "ssh" / "known_hosts").write_text("x")', False),
        ('assert not (tmp_path / "ssh").exists()', False),
        ('shutil.which("ssh")', False),
        ('open(folder / "ssh").read()', False),
        ('(folder / "ssh.py").write_text(STAND_IN)', False),
        ("engine_guard.first_on_path(aw_machine.ssh_stand_in(root, [made]))", False),
    ],
)
def test_the_scan_sees_each_way_an_ssh_stand_in_is_written(source, writes) -> None:
    assert bool(_writes_ssh(source)) == writes
