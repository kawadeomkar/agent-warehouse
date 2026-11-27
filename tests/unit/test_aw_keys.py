r"""A person's SSH keys on a machine's account (lib/claude_vm/guest/keys.py; docs/architecture/engine.md §16.10): added
by layer 1 alone, and one taken off by its fingerprint.

What adding does is `common`'s (roles/common/tasks/keys.yml, F6a), held here by its invariant: no task the system play
reaches removes a line of `authorized_keys` or writes the file whole, each task that names the file or `~/.ssh` one of
four kinds that cannot (a read, `~/.ssh` made a folder, a `lineinfile` that adds its line, a `replace` whose pattern
matches within one line and whose replacement is never empty), read through the ratchet's own walk; and a key that is
not in the list is kept through a configure, which the system scenario holds on its container (molecule/system/
side_effect.yml). No module of the new engine sends Proxmox's `sshkeys` but the create's settings call.

Taking one off: a line's key read as sshd reads it (options in front, a quoted one whole, a line commented out no
key's), its fingerprint ssh-keygen's; the pattern finds that key's lines and no other's (a longer base64 that starts
with it, another key whose option or comment names it, a line commented out); `remove` on a stand-in machine
(tests/unit/aw_machine.py), its play run by layer 1's child over the test-only local connection, takes the key's lines
and leaves every other, the file's mode and a linked `~/.ssh` as they were; a key the list names, one this computer's
ssh offers (an agent's, an identity file's, a default identity file's), and one on no line are refused with nothing
changed; the play is held to what the ratchet holds the system play to; and `deploy --remove-key` is a configure's
alone, taking the key off a VM before its system layer.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import dataclasses
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from aw_machine import Machine, layer1_host, machine, ssh_stand_in, target
from aw_machine import filed as file_key
from harness import REPO

from claude_vm.engine import configure, deploy
from claude_vm.fleet import paths
from claude_vm.guest import keys, layer1
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir

PLAY_PATH = REPO / "playbooks" / "system.yml"


# ---------------------------------------------------------------- keys made for the test ----


def _string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def _blob(seed: int, kind: str = "ssh-ed25519") -> bytes:
    """A public key's blob of kind, its 32 bytes made from seed: a key no one holds the private half of."""
    return _string(kind.encode()) + _string(bytes((seed * 7 + at) % 256 for at in range(32)))


def _key(seed: int, comment: str = "") -> str:
    data = base64.b64encode(_blob(seed)).decode()
    return f"ssh-ed25519 {data}" + (f" {comment}" if comment else "")


def _data(seed: int) -> str:
    return _key(seed).split()[1]


def _fingerprint(seed: int) -> str:
    return keys.fingerprint_of(_blob(seed))


A, B, C = 1, 2, 3


def test_a_fingerprint_is_the_one_ssh_keygen_prints(tmp_path) -> None:
    program = shutil.which("ssh-keygen")
    if program is None:
        pytest.skip("no ssh-keygen here to compare with")
    public = tmp_path / "a.pub"
    public.write_text(_key(A, "a@test") + "\n")
    printed = subprocess.run([program, "-l", "-E", "sha256", "-f", str(public)], capture_output=True, text=True)
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.split()[1] == _fingerprint(A) == keys.parsed(_key(A)).fingerprint


@pytest.mark.parametrize(
    ("line", "found"),
    [
        (_key(A), A),
        (_key(A, "a@test"), A),
        ("  " + _key(A, "a@test"), A),
        ("\t" + _key(A).replace(" ", "\t") + "\tcomment", A),
        (_key(A, "a@test") + "\r", A),
        ("no-pty " + _key(A, "a@test"), A),
        ('command="echo hello there",no-pty ' + _key(A), A),
        ('command="echo \\" quoted \\" ' + _key(B) + '" ' + _key(A), A),
        # Another key named in an option, or in the comment: the line is the key it authorises.
        ('command="echo ' + _key(A) + '" ' + _key(B), B),
        (_key(B, _key(A)), B),
        ("# " + _key(A), None),
        ("  #" + _key(A), None),
        ("", None),
        ("ssh-ed25519 AAAAnotbase64!", None),
        # A blob that names another type than the line does.
        ("ssh-rsa " + _data(A), None),
    ],
)
def test_a_line_s_key_is_read_as_sshd_reads_it(line: str, found: int | None) -> None:
    key = keys.parsed(line)
    assert (key.fingerprint if key else None) == (_fingerprint(found) if found else None)


def _matched(regexp: str, lines: list[str]) -> list[str]:
    """The lines lineinfile's absent takes for regexp: each searched with its line's end, as the module reads them."""
    compiled = re.compile(regexp)
    return [line for line in lines if compiled.search(line + "\n")]


def test_the_pattern_finds_the_key_s_lines_and_no_other_s() -> None:
    mine = [
        _key(A),
        _key(A, "a@test"),
        'no-pty,from="10.0.0.0/8" ' + _key(A, "options"),
        'command="echo two words" ' + _key(A, "quoted"),
        "\t" + _key(A).replace(" ", "\t"),
        _key(A, "crlf") + "\r",
    ]
    others = [
        _key(B, "b@test"),
        # A base64 that starts with A's, and one A's starts with.
        f"ssh-ed25519 {_data(A)}Zz longer",
        f"ssh-ed25519 {_data(A)[:-8]} shorter",
        "# " + _key(A, "commented"),
        "   # " + _key(A, "commented"),
        'command="echo ' + _key(A) + ' x" ' + _key(B, "names A in an option"),
        _key(B, _key(A)),
        "",
    ]
    regexp = keys.pattern(keys.parsed(_key(A)))
    assert _matched(regexp, mine + others) == mine
    # Within one line: nothing of it reaches over a line's end.
    assert not re.search(regexp, "\n".join(others) + "\n", re.MULTILINE)


def test_a_fingerprint_is_held_to_what_ssh_keygen_prints() -> None:
    assert keys.held(_fingerprint(A)) == _fingerprint(A)
    for wrong in ("", "SHA256:short", "MD5:" + "a" * 43, _fingerprint(A) + "\n", _fingerprint(A)[7:], None, 3):
        with pytest.raises(keys.Refused, match="ssh-keygen -l prints"):
            keys.held(wrong)


# ---------------------------------------------------------------- what this computer's ssh offers ----


@contextlib.contextmanager
def _agent(*blobs: bytes) -> Iterator[str]:
    """A stand-in agent holding blobs, at a socket of the test's own under /tmp (a socket's path is short): it answers
    each request of identities as an agent does."""
    directory = tempfile.mkdtemp(prefix="aw-agent-", dir="/tmp")
    path = os.path.join(directory, "agent")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen()
    server.settimeout(0.1)
    stop = threading.Event()
    asked: list[bytes] = []

    def serve() -> None:
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            with connection:
                asked.append(connection.recv(5))
                body = bytes([keys.AGENT_ANSWER]) + struct.pack(">I", len(blobs))
                body += b"".join(_string(blob) + _string(b"held") for blob in blobs)
                connection.sendall(struct.pack(">I", len(body)) + body)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield path
    finally:
        stop.set()
        thread.join(5)
        server.close()
        shutil.rmtree(directory, ignore_errors=True)
    assert all(each == struct.pack(">IB", 1, keys.AGENT_REQUEST) for each in asked), asked


def test_the_keys_offered_are_the_agent_s_the_identity_file_s_and_the_default_files(tmp_path) -> None:
    home = tmp_path / "person"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519.pub").write_text(_key(A, "default") + "\n")
    named = tmp_path / "elsewhere" / "work"
    named.parent.mkdir()
    named.with_name("work.pub").write_text(_key(B, "named") + "\n")
    with _agent(_blob(C)) as agent:
        env = {"HOME": str(home), "SSH_AUTH_SOCK": agent}
        assert keys.offered(env, named) == {_fingerprint(A), _fingerprint(B), _fingerprint(C)}
        assert keys.offered(env) == {_fingerprint(A), _fingerprint(C)}
    # No agent there, or none named, or no HOME: what is left.
    assert keys.offered({"HOME": str(home), "SSH_AUTH_SOCK": str(tmp_path / "gone")}) == {_fingerprint(A)}
    assert keys.offered({}, named) == {_fingerprint(B)}
    assert keys.agent_keys(str(named.with_name("work.pub"))) == ()


def test_a_key_the_list_names_or_this_computer_offers_is_refused_and_another_is_not(tmp_path) -> None:
    home = tmp_path / "person"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ecdsa.pub").write_text(_key(B, "mine") + "\n")
    env = {"HOME": str(home)}
    listed = [_key(A, "listed"), "not a key"]
    assert "is in vm_ssh_public_keys" in keys.refusal(_fingerprint(A), listed, env)
    assert "this computer's ssh offers" in keys.refusal(_fingerprint(B), listed, env)
    with _agent(_blob(C)) as agent:
        assert "this computer's ssh offers" in keys.refusal(_fingerprint(C), listed, {**env, "SSH_AUTH_SOCK": agent})
    assert keys.refusal(_fingerprint(C), listed, env) is None


# ---------------------------------------------------------------- on a stand-in machine ----


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that configures has; both gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


@dataclasses.dataclass
class Alpha:
    """A stand-in machine, and every play layer 1 was asked to run on it."""

    machine: Machine
    played: list[Path]

    @property
    def home(self) -> Path:
        return self.machine.home

    def calls(self) -> list[dict]:
        return self.machine.calls()


@pytest.fixture
def alpha(tmp_path, engine_guard, monkeypatch) -> Alpha:
    """A stand-in machine, its key filed, the stand-in ssh first on PATH, and layer 1's run given the test-only local
    connection on it (the test's alone to give): every play it was asked to run kept."""
    root = tmp_path / "machines"
    made_machine = machine(root, "alpha")
    file_key(made_machine)
    engine_guard.first_on_path(ssh_stand_in(root, [made_machine]))
    played: list[Path] = []
    real = layer1.run

    async def run(job, at, play, variables_file=None, **given):
        assert "test_connection" not in given, given
        played.append(play)
        return await real(job, at, play, variables_file, test_connection=layer1_host(made_machine), **given)

    monkeypatch.setattr(layer1, "run", run)
    return Alpha(made_machine, played)


def _env(**more: str) -> dict[str, str]:
    """The run's environment, with no agent of this computer's in it."""
    env = {name: value for name, value in os.environ.items() if name != "SSH_AUTH_SOCK"}
    return {**env, **more}


def _remove(job: jobs.Job, at, fingerprint: str, env: dict[str, str] | None = None, **given) -> keys.Removed:
    call = keys.remove(job, at, fingerprint, name="alpha", host_flag="", env=env or _env(), **given)
    return asyncio.run(call)


KEPT = [
    _key(B, "b@test"),
    f"ssh-ed25519 {_data(A)}Zz longer",
    "# " + _key(A, "commented, kept"),
    'command="echo ' + _key(A) + ' x" ' + _key(C, "names A in an option"),
    "",
]
TAKEN = [_key(A, "first"), 'no-pty,command="echo hi there" ' + _key(A, "second")]


def _keys_file(made_machine, linked: bool) -> Path:
    """The machine's keys file, its lines KEPT with TAKEN among them, mode 0640: through a linked ~/.ssh where asked."""
    folder = made_machine.home / (".ssh-elsewhere" if linked else ".ssh")
    folder.mkdir(mode=0o700, exist_ok=True)
    if linked:
        (made_machine.home / ".ssh").symlink_to(folder.name)
    path = folder / "authorized_keys"
    lines = [KEPT[0], TAKEN[0], *KEPT[1:3], TAKEN[1], *KEPT[3:]]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o640)
    return path


@pytest.mark.parametrize("linked", [False, True], ids=["folder", "linked"])
def test_remove_takes_the_key_s_lines_and_leaves_the_rest_its_mode_and_a_link(made, alpha, linked) -> None:
    path = _keys_file(alpha, linked)
    removed = _remove(made, target(alpha.machine), _fingerprint(A), listed=[_key(B, "listed")])
    assert removed == keys.Removed(_fingerprint(A), 2)
    assert path.read_text() == "\n".join(KEPT) + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert (alpha.home / ".ssh").is_symlink() is linked and (alpha.home / ".ssh").is_dir()
    assert alpha.played == [keys.PLAY]
    # The account's file was read over the runner, before and after, by its path from the account's home.
    cats = [call["argv"][-1] for call in alpha.calls() if call["outcome"] == "ran"]
    assert cats == ["cat -- .ssh/authorized_keys"] * 2, cats


def test_a_key_on_no_line_listed_or_offered_is_refused_with_nothing_changed(made, alpha, tmp_path) -> None:
    path = _keys_file(alpha, False)
    before = path.read_text()
    with pytest.raises(keys.Refused, match=r"no line of ~/\.ssh/authorized_keys on alpha holds the key"):
        _remove(made, target(alpha.machine), keys.fingerprint_of(_blob(9)))
    with pytest.raises(keys.Refused, match="is in vm_ssh_public_keys"):
        _remove(made, target(alpha.machine), _fingerprint(A), listed=[TAKEN[0]])
    # The run's own key: the agent holds it, or the identity file the target names is its.
    with _agent(_blob(A)) as agent, pytest.raises(keys.Refused, match="this computer's ssh offers"):
        _remove(made, target(alpha.machine), _fingerprint(A), _env(SSH_AUTH_SOCK=agent))
    identity = tmp_path / "id_work"
    identity.write_text("")
    identity.with_name("id_work.pub").write_text(_key(A, "work") + "\n")
    with pytest.raises(keys.Refused, match="this computer's ssh offers"):
        _remove(made, target(alpha.machine, identity_file=identity), _fingerprint(A))
    with pytest.raises(keys.Refused, match="ssh-keygen -l prints"):
        _remove(made, target(alpha.machine), "SHA256:nope")
    assert path.read_text() == before and alpha.played == []
    # A refusal before the file is read reads nothing on the machine.
    assert [call["argv"][-1] for call in alpha.calls() if call["outcome"] == "ran"] == ["cat -- .ssh/authorized_keys"]


def test_a_keys_file_that_cannot_be_read_fails_with_nothing_changed(made, alpha) -> None:
    with pytest.raises(keys.Failed, match=r"~/\.ssh/authorized_keys on alpha could not be read"):
        _remove(made, target(alpha.machine), _fingerprint(A))
    assert alpha.played == []


# ---------------------------------------------------------------- the play, held as the system play is ----


def test_the_key_s_play_is_one_built_in_task_alone_in_its_folder() -> None:
    import test_aw_layer1_ratchet as ratchet

    assert sorted(path.name for path in keys.PLAY.parent.iterdir()) == [keys.PLAY.name]
    [play] = yaml.safe_load(keys.PLAY.read_text())
    [task] = play["tasks"]
    assert play["hosts"] == "all" and play["become"] is False
    assert set(play) == {"name", "hosts", "gather_facts", "become", "tasks"}
    assert task["ansible.builtin.lineinfile"] == {
        "path": "~/.ssh/authorized_keys",
        "regexp": "{{ " + keys.PATTERN_VARIABLE + " }}",
        "state": "absent",
    }
    assert ratchet.outside(keys.PLAY, REPO) == {} and ratchet.commands(keys.PLAY, REPO) == {}
    assert ratchet.setting_connection_variables(keys.PLAY, REPO) == []
    assert ratchet.reaching_this_computer(keys.PLAY, REPO, lookups={}) == []
    # Layer 1 takes its variables file, holding the pattern alone.
    assert layer1.PLAY_NAMES[keys.PLAY] == keys.NAMES == {keys.PATTERN_VARIABLE}


# ---------------------------------------------------------------- the invariants ----

# What a task that names the keys file or ~/.ssh may be: one of these, each held below to what keeps every line.
READS = frozenset(f"{prefix}{name}" for prefix in ("", "ansible.builtin.") for name in ("assert", "stat"))
FOLDER = frozenset({"file", "ansible.builtin.file"})
ADDS = frozenset({"lineinfile", "ansible.builtin.lineinfile"})
REPLACES = frozenset({"replace", "ansible.builtin.replace"})
# What names the keys: the file, or the folder that holds it.
NAMES_KEYS = re.compile(r"authorized_keys|\.ssh\b")
_JINJA = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}", re.DOTALL)
_BARE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _mentions(value: object, names: set[str]) -> bool:
    """Whether value, any string of it, names the keys or one of the variables names that do."""
    text = json.dumps(value, default=str)
    return bool(NAMES_KEYS.search(text)) or any(re.search(rf"\b{re.escape(name)}\b", text) for name in names)


def _naming_variables(found) -> set[str]:
    """Every variable of the roles' defaults and vars, and of a task's vars, whose value names the keys, followed
    through each variable that names such a variable."""
    values: dict[str, object] = {}
    for path in found.variables:
        loaded = yaml.safe_load(path.read_text()) if path.is_file() else None
        if isinstance(loaded, dict):
            values.update(loaded)
    for _, task in found.entries:
        values.update(task.get("vars") or {})
    names: set[str] = set()
    while True:
        more = {name for name, value in values.items() if name not in names and _mentions(value, names)}
        if not more:
            return names
        names |= more


def _inlined(text: str, variables: dict[str, object]) -> str:
    """text with each template that is one of variables, bare, put in its place, as often as one names another."""
    for _ in range(10):
        changed = _JINJA.sub(lambda found: str(variables[found[1]]) if found[1] in variables else found[0], text)
        if changed == text:
            return text
        text = changed
    raise AssertionError(f"a template names itself: {text}")


def _crosses_a_line(regexp: str) -> list[str]:
    """What of regexp, as Python's re reads it under MULTILINE (as replace compiles it), can match a line's end: each
    escaped value a template gives (regex_escape) read as a literal that cannot."""
    from re import _constants as const
    from re import _parser as parser

    held: list[str] = []
    rest = _JINJA.sub(
        lambda found: "x" if found[1].rstrip().endswith("| regex_escape") else held.append(found[0]) or "x", regexp
    )
    if held:
        return [f"a template whose value is not regex_escape'd: {each}" for each in held]
    tree = parser.parse(rest, re.MULTILINE)
    newline = ord("\n")
    found: list[str] = []

    def in_class(items) -> bool:
        negated = any(op is const.NEGATE for op, _ in items)
        inside = False
        for op, value in items:
            if op is const.LITERAL:
                inside |= value == newline
            elif op is const.RANGE:
                inside |= value[0] <= newline <= value[1]
            elif op is const.CATEGORY:
                inside |= value in (
                    const.CATEGORY_SPACE,
                    const.CATEGORY_NOT_DIGIT,
                    const.CATEGORY_NOT_WORD,
                    const.CATEGORY_LINEBREAK,
                )
        return inside != negated

    def walk(items, flags: int) -> None:
        for op, value in items:
            if op in (const.ASSERT, const.ASSERT_NOT, const.AT):
                continue
            if op is const.LITERAL and value == newline:
                found.append("a literal line's end")
            elif op is const.NOT_LITERAL and value != newline:
                found.append("a character but one, which a line's end is")
            elif op is const.ANY and flags & re.DOTALL:
                found.append("a dot under DOTALL")
            elif op is const.IN and in_class(value):
                found.append("a class that holds a line's end")
            elif op in (const.MAX_REPEAT, const.MIN_REPEAT, const.POSSESSIVE_REPEAT):
                walk(value[2], flags)
            elif op is const.SUBPATTERN:
                walk(value[3], (flags | value[1]) & ~value[2])
            elif op is const.BRANCH:
                for branch in value[1]:
                    walk(branch, flags)
            elif op is const.ATOMIC_GROUP:
                walk(value, flags)
            elif op not in (const.LITERAL, const.NOT_LITERAL, const.ANY, const.IN, const.GROUPREF):
                found.append(f"a part the scan does not read: {op}")

    walk(tree, tree.state.flags)
    return found


def _takes_a_line(action: str, args: dict, variables: dict[str, object]) -> str | None:
    """Why a task that names the keys could take a line of authorized_keys away or write it whole, or None."""
    if action in READS:
        return None
    if action in FOLDER:
        return None if args.get("state") == "directory" else f"file with state {args.get('state')!r}"
    if action in ADDS:
        if args.get("state", "present") != "present" or not str(args.get("line") or ""):
            return "a lineinfile that takes a line away, or adds none"
        return None
    if action in REPLACES:
        replacement = str(args.get("replace") or "")
        if not replacement.strip():
            return "a replace with no replacement, which takes what it matches away"
        crossing = _crosses_a_line(_inlined(str(args.get("regexp") or ""), variables))
        return f"a replace whose pattern can reach over a line's end: {', '.join(crossing)}" if crossing else None
    return f"{action}, which may write the file whole or take a line away"


def _keys_tasks(play: Path = PLAY_PATH) -> dict[tuple[str, str], str | None]:
    """Each task the play reaches that names the keys, by its file and name: why it could take a line away, or None."""
    import test_aw_layer1_ratchet as ratchet

    found = ratchet.walk(play, REPO)
    names = _naming_variables(found)
    tasks = {}
    for path, task in found.entries:
        if any(key in task for key in ratchet.BLOCK):
            continue
        action, args, _ = ratchet._parsed(task)
        if _mentions(args, names) or _mentions(task.get("vars") or {}, names):
            variables = {name: value for name, value in (task.get("vars") or {}).items()}
            tasks[(str(path.relative_to(REPO)), task.get("name", ""))] = _takes_a_line(action, args, variables)
    return tasks


def test_no_task_the_system_play_reaches_takes_a_line_of_authorized_keys_or_writes_it_whole() -> None:
    found = _keys_tasks()
    # The keys' tasks are reached, so the scan reads them.
    assert {name for _, name in found} >= {
        "keys | Make ~/.ssh, its target where it is a link",
        "keys | Make every line of a listed key its listed form",
        "keys | Authorise each SSH key",
    }, found
    assert {key: why for key, why in found.items() if why is not None} == {}


@pytest.mark.parametrize(
    ("task", "why"),
    [
        (
            {"ansible.builtin.lineinfile": {"path": "~/.ssh/authorized_keys", "regexp": "x", "state": "absent"}},
            "takes a line away",
        ),
        ({"ansible.builtin.replace": {"path": "~/.ssh/authorized_keys", "regexp": "^x.*$\\n"}}, "no replacement"),
        (
            {"ansible.builtin.replace": {"path": "~/.ssh/authorized_keys", "regexp": "^x.*$", "replace": ""}},
            "no replacement",
        ),
        (
            {"ansible.builtin.replace": {"path": "~/.ssh/authorized_keys", "regexp": "^x\\s+y$", "replace": "z"}},
            "a class that holds a line's end",
        ),
        (
            {"ansible.builtin.replace": {"path": "~/.ssh/authorized_keys", "regexp": "^x[^#]*y$", "replace": "z"}},
            "a character but one",
        ),
        (
            {"ansible.builtin.replace": {"path": "~/.ssh/authorized_keys", "regexp": "(?s)^x.*y$", "replace": "z"}},
            "DOTALL",
        ),
        (
            {"ansible.builtin.replace": {"path": "~/.ssh/authorized_keys", "regexp": "^{{ v }}$", "replace": "z"}},
            "not regex_escape'd",
        ),
        ({"ansible.builtin.copy": {"dest": "~/.ssh/authorized_keys", "content": "x", "mode": "0600"}}, "whole"),
        ({"ansible.builtin.file": {"path": "~/.ssh", "state": "absent"}}, "state 'absent'"),
        ({"ansible.builtin.command": {"argv": ["sed", "-i", "/x/d", "{{ common_keys_path }}"]}}, "take a line away"),
    ],
)
def test_the_scan_refuses_each_task_that_could_take_a_line_away(tmp_path, task, why) -> None:
    """Each planted in a role of the test's own, which a play of the test's own runs: the scan says why."""
    role = tmp_path / "roles" / "planted"
    (role / "tasks").mkdir(parents=True)
    (role / "defaults").mkdir()
    (role / "defaults" / "main.yml").write_text(yaml.safe_dump({"common_keys_path": "~/.ssh/authorized_keys"}))
    (role / "tasks" / "main.yml").write_text(yaml.safe_dump([{"name": "planted", **task}]))
    (tmp_path / "playbooks").mkdir()
    play = tmp_path / "playbooks" / "play.yml"
    play.write_text(yaml.safe_dump([{"name": "p", "hosts": "all", "roles": ["planted"]}]))
    import test_aw_layer1_ratchet as ratchet

    found = ratchet.walk(play, tmp_path)
    names = _naming_variables(found)
    [(path, entry)] = [(path, each) for path, each in found.entries if each.get("name") == "planted"]
    action, args, _ = ratchet._parsed(entry)
    assert _mentions(args, names)
    said = _takes_a_line(action, args, {})
    assert said is not None and why in said, said


def test_the_scan_takes_the_keys_replace_as_it_is_and_reads_its_variables() -> None:
    """F6a's replace, its pattern made of its task's variables and escaped values alone, matches within one line."""
    task = yaml.safe_load((REPO / "roles" / "common" / "tasks" / "keys.yml").read_text())
    [replace] = [each for each in task if "ansible.builtin.replace" in each]
    regexp = _inlined(replace["ansible.builtin.replace"]["regexp"], replace["vars"])
    assert "{{ common_key_line }}" not in regexp and _crosses_a_line(regexp) == []
    assert _crosses_a_line(regexp.replace("[ \\t]", "\\s")) != []


# Each module of the new engine that names Proxmox's sshkeys, with why: the create's settings call alone sends it.
SSHKEYS = {
    "lib/claude_vm/engine/create.py": "settings_call: the create's settings call, before the VM's first start",
    "lib/claude_vm/pve/client.py": "how the client encodes the value a write is given, percent-encoded whole",
    "lib/claude_vm/pve/privileges.py": "the privilege a settings call needs, in the one table",
    "lib/claude_vm/cli/status_inputs.py": "status's own read of what a VM should hold, compared and never written",
}
NEW_ENGINE = ("schema", "cli", "engine", "pve", "ssh", "vendors", "runs", "fleet", "guest", "control")


def _naming_sshkeys(source: str) -> list[tuple[int, str]]:
    """Each place source names sshkeys in a string but a docstring, with the function it is in ("" at the module's
    level)."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else function
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and id(child) not in docstrings:
                if "sshkeys" in child.value:
                    found.append((child.lineno, function))
            visit(child, inner)

    visit(tree, "")
    return found


def test_no_workflow_sends_sshkeys_but_the_create_s_settings_call() -> None:
    found = {}
    for package in NEW_ENGINE:
        for path in sorted((REPO / "lib" / "claude_vm" / package).rglob("*.py")):
            if where := _naming_sshkeys(path.read_text()):
                found[str(path.relative_to(REPO))] = where
    assert set(found) <= set(SSHKEYS), set(found) - set(SSHKEYS)
    # In the create, inside its settings call alone; and that call made once, by the create's own provision.
    create = found["lib/claude_vm/engine/create.py"]
    assert create and {function for _, function in create if function} == {"settings_call"}, create
    calls = [
        str(path.relative_to(REPO))
        for package in NEW_ENGINE
        for path in (REPO / "lib" / "claude_vm" / package).rglob("*.py")
        if re.search(r"\bsettings_call\(", path.read_text()) and path.name != "create.py"
    ]
    assert calls == [], calls


def test_the_scan_sees_sshkeys_named_in_a_workflow() -> None:
    assert _naming_sshkeys('def f(c):\n    c.write_config(o, {"sshkeys": k})\n') == [(2, "f")]
    assert _naming_sshkeys('KEY = "sshkeys"\n') == [(1, "")]


# ---------------------------------------------------------------- deploy --remove-key ----


def test_remove_key_is_a_configure_s_alone_and_its_fingerprint_is_held() -> None:
    fingerprint = _fingerprint(A)
    for values in ({"only": "provision"}, {"names": ["delta"]}, {"only": "template"}):
        sentence = deploy.not_yet({**values, "remove_key": fingerprint})
        assert sentence is not None and "--remove-key takes a key off" in sentence and "--only configure" in sentence
        assert fingerprint not in sentence
    assert deploy.not_yet({"only": "configure", "remove_key": fingerprint}) is None
    assert deploy.not_yet({"names": ["delta"], "tags": "common", "remove_key": fingerprint}) is None
    assert configure.Plan.read(None, False, fingerprint).remove_key == fingerprint
    assert configure.Plan.read().remove_key == ""
    with pytest.raises(ValueError, match="ssh-keygen -l prints"):
        configure.Plan.read(None, False, "SHA256:x")


def test_a_configure_takes_the_key_off_before_its_system_layer(made_fleet, fleet_job, fleet_heard, swapped) -> None:
    from test_aw_configure import _configured, _provisioned, _reports

    [(_, delta)] = _provisioned(made_fleet, "delta").values()
    swapped.machines[delta.identity] = delta
    path = _keys_file(delta, False)
    [done] = _configured(("delta",), configure.Plan.read(None, False, _fingerprint(A)), fleet_job)
    assert done.outcome == "succeeded", done
    assert path.read_text() == "\n".join(KEPT) + "\n"
    assert [call["play"] for call in swapped.calls] == [keys.PLAY, swapped.play]
    assert f"delta: the key {_fingerprint(A)} was taken off its account (2 lines of ~/.ssh/authorized_keys)." in (
        _reports(fleet_heard)
    )
    # Again: no line holds it now, so the VM fails with why, and nothing more runs on it.
    [again] = _configured(("delta",), configure.Plan.read(None, False, _fingerprint(A)), fleet_job)
    assert again.outcome == "failed" and [call["play"] for call in swapped.calls][2:] == []
    assert path.read_text() == "\n".join(KEPT) + "\n"


@pytest.fixture
def made_fleet(home, tmp_path):
    from pki import proxmox_pki
    from test_aw_create import _making

    yield from _making(home, tmp_path, proxmox_pki(tmp_path / "pki", key_usage=True), True)


@pytest.fixture
def fleet_job(made_fleet):
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy delta", "pve", ["delta"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


@pytest.fixture
def fleet_heard(fleet_job) -> list[jobs.Event]:
    found: list[jobs.Event] = []
    fleet_job.listen(found.append)
    return found


@pytest.fixture
def swapped(monkeypatch, tmp_path, made_fleet, engine_guard):
    from aw_machine import SSH_FOLDER
    from test_aw_configure import Swapped

    engine_guard.first_on_path(made_fleet.root / SSH_FOLDER)
    return Swapped(monkeypatch, tmp_path / "play", True)
