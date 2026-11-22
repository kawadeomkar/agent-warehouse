"""The Proxmox client's writes (lib/claude_vm/pve/client.py and answers.py; docs/architecture/engine.md §16.5).

Every write goes to the fake Proxmox (tests/molecule/fake_pve_api.py) through harness.fake_fleet with the engine's pool
and its one token, granted as init grants them and held to the engine's table of privileges (enforce): every request a
write sends is a call the table has a row for, answered only where the token holds that row's privilege, so a write
the token may not make is Denied by the fake, as Proxmox refuses it, never by a refusal planted for the test. The
template and alpha are the pool's members; beta and gamma are not.

A clone into the pool with a description is proved by the new VM's own configuration. A clone onto a VMID that is
taken is Exists, at the call or in its task in each of Proxmox's three sentences, and nothing is ever deleted at that
VMID. A configuration's write sends the digest its read gave, and a stale one is DigestMismatch with nothing written;
edit_config reads and applies again, three times at most. A write to a VM a clone or a deletion holds is Locked, and
tried once. A write whose answer is cut is the transport's Unknown, sent once. A task that fails raises with the last
lines of its log, made plain; one that runs past its deadline raises then, and is not waited for longer; one whose run
stops is Stopped, between polls or during one, and goes on on Proxmox, unfollowed. The rules with a test of their own:
every method that sends a PUT to a configuration's path sends digest (a syntax-tree test of the module); a
configuration's write sends only the keys whose privileges are the table's rows for it; every row of the table names a
method of the client; and no error of a write, nor of a read, says the token's secret, even where Proxmox's words echo
it.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import logging
import socket
import threading
import time
import urllib.parse
from pathlib import Path

import pytest
from harness import (
    ENGINE_POOL,
    ENGINE_TOKEN,
    ENGINE_USER,
    FAKES,
    REPO,
    _pve,
    _pve_requests,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from test_aw_transport import _said, _Tunnel

from claude_vm.pve import answers, pin, privileges
from claude_vm.pve.client import (
    CONFIG_KEYS,
    DEADLINES,
    EDITS,
    LOCK,
    REFUSED_STATUS,
    STALE,
    Denied,
    DigestMismatch,
    Exists,
    Locked,
    Proxmox,
    Refused,
    Stopped,
    Task,
    TaskFailed,
    TaskTimedOut,
    Token,
    Unknown,
    _taken,
)
from claude_vm.secret import MARK, Secret
from claude_vm.vendors.transport import Answer, Unreachable

# The module's certificate authority, which signs the fake's certificate: cheap enough to make again on each worker
# (tests/unit/conftest.py).
EACH_WORKER = {"pki"}
CLIENT = REPO / "lib" / "claude_vm" / "pve" / "client.py"
# A token's secret no error may say: low in entropy, so that gitleaks takes it for no key.
CANARY = "unit-test-pve-writes-canary-value"
# What the fake says where it refuses, as it says it: the constants at its top, which experiment 6 corrects (A21).
SAID = {
    target.id: node.value.value
    for node in ast.parse((FAKES / "fake_pve_api.py").read_text()).body
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    for target in node.targets
    if isinstance(target, ast.Name)
}
KEYS = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse+/= dev@mac\nssh-ed25519 AAAAOtherTestKey other@host"


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@dataclasses.dataclass
class Fleet:
    """The fake at base, its state in directory, the engine's token, and the certificates it serves (pki)."""

    base: str
    directory: Path
    token: Token
    pki: dict

    def asked(self) -> list[dict]:
        """Every request the client sent the fake: those of its user agent, in order."""
        return [entry for entry in _pve_requests(self.directory) if entry["agent"] == pin.USER_AGENT]

    def writes(self) -> list[tuple[str, str]]:
        """The method and the path below the API of every request the client sent that was not a GET."""
        return [
            (entry["method"], entry["path"].removeprefix("/api2/json/"))
            for entry in self.asked()
            if entry["method"] != "GET"
        ]

    def sent(self, method: str, path: str) -> list[dict]:
        """The parameters of each request of the client's of method to path below the API."""
        return [
            entry["params"]
            for entry in self.asked()
            if entry["method"] == method and entry["path"] == f"/api2/json/{path}"
        ]

    def deletions(self) -> list[dict]:
        """Every DELETE the fake was sent, by anyone."""
        return [entry for entry in _pve_requests(self.directory) if entry["method"] == "DELETE"]

    def client(self, **kwargs) -> Proxmox:
        return Proxmox(self.base, pin.kept(self.pki["ca"]), self.token, **kwargs)

    def raw(self, vmid: int) -> dict:
        """A VM's configuration as today's token reads it."""
        return _pve(self.base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]

    def tasks(self) -> dict:
        """Every task the fake made, by its UPID, as its tasks.json records them."""
        return json.loads((self.directory / "pve" / "tasks.json").read_text())


@pytest.fixture
def fleet(tmp_path, pki):
    """The fake with the engine's token held to its table, the template and alpha the pool's members, and granted
    beside the role on the pool what init grants by default (card E1): VM.Audit on /vms, and what the table needs on the
    storage."""
    certificate = (pki["cert"], pki["key"])
    for base in fake_fleet(tmp_path, engine=True, certificate=certificate, enforce=True):
        _pve(base, "PUT", "pools", poolid=ENGINE_POOL, vms="9000,9010")
        engine_grant(base, "/vms", "VM.Audit")
        engine_grant(base, "/storage/local-lvm", ",".join(sorted(privileges.needed("storage"))))
        token_id, secret = engine_token(tmp_path)
        yield Fleet(base, tmp_path, Token(token_id, Secret(secret)), pki)


def _cloned(client: Proxmox, newid: int = 9020, name: str = "delta", **kwargs) -> Task:
    """A clone of the template onto newid in the pool, followed to its end."""
    task = client.clone("pve", 9000, newid, name, ENGINE_POOL, **kwargs)
    client.follow(task)
    return task


# --------------------------------------------------------------------------------------------- a clone ----


def test_a_clone_into_the_pool_with_a_description_is_proved_by_its_own_config(fleet) -> None:
    with fleet.client() as client:
        task = client.clone("pve", 9000, 9020, "delta", ENGINE_POOL, storage="local-lvm", description="aw: cloning")
        assert (task.node, task.kind, task.vmid) == ("pve", "clone", 9020) and answers.UPID.fullmatch(task.upid)
        assert client.follow(task).ok
        config = client.config("pve", 9020)
        template = client.config("pve", 9000)
        members = [member.vmid for member in client.pool(ENGINE_POOL).members]
    assert (config.name, config.description, config.lock, config.template) == ("delta", "aw: cloning", "", False)
    assert config.identity and config.identity != template.identity and config.mac
    assert 9020 in members
    assert fleet.sent("POST", "nodes/pve/qemu/9000/clone") == [
        {
            "newid": "9020",
            "name": "delta",
            "pool": ENGINE_POOL,
            "full": "1",
            "storage": "local-lvm",
            "description": "aw: cloning",
        }
    ]


def test_a_clone_onto_a_vmid_taken_at_the_call_is_exists_and_nothing_is_deleted(fleet) -> None:
    with fleet.client() as client, pytest.raises(Exists) as taken:
        client.clone("pve", 9000, 9010, "again", ENGINE_POOL)
    assert taken.value.newid == 9010 and "next free VMID" in str(taken.value)
    assert fleet.raw(9010)["name"] == "alpha" and fleet.deletions() == []


@pytest.mark.parametrize("held", ["a VM here", "a running clone", "a container", "another node's VM"])
def test_a_clone_whose_vmid_its_task_finds_taken_is_exists_in_each_of_proxmox_s_words_and_nothing_is_deleted(
    fleet, held
) -> None:
    newid, words = {
        "a VM here": (9010, SAID["CLONE_EXISTS"].format(newid=9010)),
        "a running clone": (9021, SAID["CLONE_LOCK_TIMEOUT"].format(newid=9021)),
        "a container": (9003, SAID["CLONE_TAKEN"].format(kind="CT", newid=9003, node="pve")),
        "another node's VM": (8001, SAID["CLONE_TAKEN"].format(kind="VM", newid=8001, node="pve2")),
    }[held]
    with fleet.client() as client:
        winner = None
        if held == "a running clone":
            _pve(fleet.base, "PUT", "fake/task-polls", polls=50)
            winner = client.clone("pve", 9000, newid, "winner", ENGINE_POOL)
            _pve(fleet.base, "PUT", "fake/task-polls", polls=0)
        if held in ("a VM here", "a running clone"):
            # The race's loser: its call was checked before the winner's VM was there, and its task fails.
            _pve(fleet.base, "PUT", "fake/clone-race", clone=1, config=0)
        loser = client.clone("pve", 9000, newid, "loser", ENGINE_POOL)
        with pytest.raises(Exists) as taken:
            client.follow(loser)
        assert taken.value.newid == newid
        assert fleet.tasks()[loser.upid]["error"] == words
        if winner is not None:
            _pve(fleet.base, "PUT", "fake/task-end", upid=winner.upid)
            assert client.follow(winner).ok and client.config("pve", newid).name == "winner"
    # Never a DELETE at the VMID asked for, which on a host would be the winner's VM.
    assert fleet.deletions() == []


# ------------------------------------------------------------------------------- a configuration's write ----


def test_a_write_sends_the_digest_its_read_gave_and_a_stale_one_writes_nothing(fleet) -> None:
    with fleet.client() as client:
        read = client.config("pve", 9010)
        assert client.write_config("pve", 9010, {"description": "one", "cores": 4}, read.digest) is None
        with pytest.raises(DigestMismatch) as stale:
            client.write_config("pve", 9010, {"description": "two"}, read.digest)
        after = client.config("pve", 9010)
    assert stale.value.vmid == 9010 and "changed since it was read" in str(stale.value)
    assert (after.description, after.values["cores"]) == ("one", "4")
    assert [params["digest"] for params in fleet.sent("PUT", "nodes/pve/qemu/9010/config")] == [read.digest] * 2


def test_two_edit_config_calls_on_one_vm_both_land_the_second_after_a_re_read(fleet) -> None:
    _pve(fleet.base, "PUT", "nodes/pve/qemu/9010/config", description="notes")
    seen: list[str] = []
    with fleet.client() as client:

        def first(config: answers.VmConfig) -> dict:
            return {"description": f"{config.description}\nfirst"}

        def second(config: answers.VmConfig) -> dict:
            seen.append(config.digest)
            if len(seen) == 1:
                # The other writer lands between this read and its write.
                assert client.edit_config("pve", 9010, first) == {"description": "notes\nfirst"}
            return {"description": f"{config.description}\nsecond"}

        assert client.edit_config("pve", 9010, second) == {"description": "notes\nfirst\nsecond"}
        assert client.config("pve", 9010).description == "notes\nfirst\nsecond"
    assert len(seen) == 2 and seen[0] != seen[1]
    puts = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert [params["description"] for params in puts] == ["notes\nfirst", "notes\nsecond", "notes\nfirst\nsecond"]
    assert puts[1]["digest"] == seen[0] and puts[2]["digest"] == seen[1]


def test_a_writer_that_always_loses_raises_after_three_tries(fleet) -> None:
    applied = []

    def loses(config: answers.VmConfig) -> dict:
        applied.append(config.digest)
        # Another writer, every time, between this read and its write.
        _pve(fleet.base, "PUT", "nodes/pve/qemu/9010/config", cores=len(applied))
        return {"description": "never"}

    with fleet.client() as client, pytest.raises(DigestMismatch):
        client.edit_config("pve", 9010, loses)
    assert len(applied) == EDITS == 3 and len(set(applied)) == 3
    assert len(fleet.sent("PUT", "nodes/pve/qemu/9010/config")) == 3
    assert fleet.raw(9010).get("description", "") != "never"


def test_an_edit_that_asks_for_nothing_writes_nothing(fleet) -> None:
    with fleet.client() as client:
        assert client.edit_config("pve", 9010, lambda config: None) == {}
        assert client.edit_config("pve", 9010, lambda config: {}) == {}
    assert fleet.writes() == []


def test_sshkeys_are_sent_percent_encoded_whole_and_read_back_as_proxmox_keeps_them(fleet) -> None:
    encoded = urllib.parse.quote(KEYS, safe="")
    with fleet.client() as client:
        client.edit_config("pve", 9010, lambda config: {"sshkeys": KEYS, "ciuser": "dev", "ipconfig0": "ip=dhcp"})
        kept = client.config("pve", 9010).values["sshkeys"]
    (sent,) = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert sent["sshkeys"] == kept == encoded and urllib.parse.unquote(kept) == KEYS
    assert "%0A" in encoded and "%2B" in encoded and " " not in encoded


def test_tags_are_sent_comma_joined_and_read_back_in_either_spelling(fleet) -> None:
    with fleet.client() as client:
        client.edit_config("pve", 9010, lambda config: {"tags": ["agent-warehouse", "aw-made"]})
        tags = client.config("pve", 9010).tags
    (sent,) = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert sent["tags"] == "agent-warehouse,aw-made" and fleet.raw(9010)["tags"] == "agent-warehouse;aw-made"
    assert tags == ("agent-warehouse", "aw-made")


def test_a_key_removed_is_sent_in_delete(fleet) -> None:
    with fleet.client() as client:
        client.edit_config("pve", 9010, lambda config: {"delete": ["ciuser", "onboot"]})
    (sent,) = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert sent["delete"] == "ciuser,onboot" and not {"ciuser", "onboot"} & set(fleet.raw(9010))


# ------------------------------------------------------------------------------------- a VM Proxmox holds ----


def test_a_write_while_a_deletion_holds_the_vm_is_locked_and_tried_once(fleet) -> None:
    with fleet.client() as client:
        _cloned(client)
        _pve(fleet.base, "PUT", "fake/task-polls", polls=3)
        deletion = client.delete("pve", 9020)
        with pytest.raises(Locked) as locked:
            client.edit_config("pve", 9020, lambda config: {"description": "late"})
        assert (locked.value.vmid, locked.value.lock) == (9020, SAID["DELETION_LOCK"])
        assert client.follow(deletion).ok
    assert len(fleet.sent("PUT", "nodes/pve/qemu/9020/config")) == 1


def test_a_write_during_a_running_clone_is_locked(fleet) -> None:
    """The engine's own clone joins the pool when its task ends, so until then its token holds nothing on the new VM
    and Proxmox refuses it a write by its permission check, before the lock: Denied. Made a member while the clone
    runs, as root may, the write meets the clone's lock: Locked, which once the task has ended is gone."""
    with fleet.client() as client:
        _pve(fleet.base, "PUT", "fake/task-polls", polls=50)
        clone = client.clone("pve", 9000, 9020, "delta", ENGINE_POOL)
        read = client.config("pve", 9020)
        assert read.lock == SAID["CLONE_LOCK"]
        with pytest.raises(Denied):
            client.write_config("pve", 9020, {"description": "early"}, read.digest)
        _pve(fleet.base, "PUT", "fake/pool-member", vmid=9020, pool=ENGINE_POOL)
        with pytest.raises(Locked) as locked:
            client.write_config("pve", 9020, {"description": "early"}, read.digest)
        assert (locked.value.vmid, locked.value.lock) == (9020, "clone") and "try again" in str(locked.value)
        _pve(fleet.base, "PUT", "fake/task-end", upid=clone.upid)
        assert client.follow(clone).ok
        client.edit_config("pve", 9020, lambda config: {"description": "landed"})
        assert client.config("pve", 9020).description == "landed"


# ------------------------------------------------------------------------------------- a write sent once ----


class _Cutting(_Tunnel):
    """A CONNECT proxy that carries every request on to the fake and, from the moment the fake has recorded the request
    cut() names, carries nothing more back: that request's answer is cut, the connection closed. The fake records a
    request once it has read it whole, before it answers, so the request has reached it."""

    def __init__(self, cut) -> None:
        self.cut = cut
        super().__init__()

    def _pump(self, source: socket.socket, sink: socket.socket, keep: bool) -> None:
        if keep:
            return super()._pump(source, sink, keep)
        try:
            while data := source.recv(65536):
                if self.cut():
                    break
                sink.sendall(data)
        except OSError:
            pass
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return None


def test_a_post_whose_answer_is_cut_is_unknown_and_the_fake_saw_it_once(fleet) -> None:
    def cloned() -> bool:
        return any(entry["path"].endswith("/clone") for entry in fleet.asked())

    tunnel = _Cutting(cloned)
    try:
        with fleet.client(proxy=tunnel.url) as client, pytest.raises(Unknown) as unknown:
            client.clone("pve", 9000, 9020, "delta", ENGINE_POOL)
    finally:
        tunnel.close()
    assert "may or may not have been carried out" in str(unknown.value)
    assert fleet.writes() == [("POST", "nodes/pve/qemu/9000/clone")]
    # It was carried out: the workflow knows by reading the VM again, never by sending the clone twice.
    assert fleet.raw(9020)["name"] == "delta"


# ----------------------------------------------------------------------------------------------- tasks ----


def test_a_failed_task_raises_with_the_last_lines_of_its_log_made_plain(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-fail", error="start failed: \x1b[31mno\x1b[0m\x07 room", type="qmstart")
    with fleet.client() as client:
        task = client.start("pve", 9010)
        with pytest.raises(TaskFailed) as failed:
            client.follow(task)
    assert failed.value.task == task and failed.value.error == "start failed: no room"
    assert failed.value.lines == ("TASK ERROR: start failed: no room",)
    assert str(failed.value) == (
        "Proxmox's start task for VM 9010 failed: start failed: no room"
        " (its log ends: TASK ERROR: start failed: no room)"
    )
    assert fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/log") == [{"start": "0", "limit": "500"}]


def test_a_task_past_its_deadline_raises_and_is_not_waited_for_longer(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start("pve", 9010)
        began = time.monotonic()
        with pytest.raises(TaskTimedOut) as late:
            client.follow(task, deadline=0.6)
        took = time.monotonic() - began
    assert late.value.task == task and late.value.seconds == 0.6 and "read the VM to know" in str(late.value)
    assert 0.6 <= took < 2.0, took
    polls = fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/status")
    assert 2 <= len(polls) <= 8, len(polls)
    assert DEADLINES["clone"] == 600.0 and set(DEADLINES) == answers.TASK_KINDS


def _following(client: Proxmox, task: Task, stop: threading.Event) -> tuple[threading.Thread, dict]:
    """follow() in a thread of its own, as a workflow runs it through asyncio.to_thread: what it raised or returned, and
    when, once the thread ends."""
    ended: dict = {}

    def follow() -> None:
        try:
            ended["returned"] = client.follow(task, stop=stop)
        except Exception as error:
            ended["raised"] = error
        ended["at"] = time.monotonic()

    thread = threading.Thread(target=follow, daemon=True)
    thread.start()
    return thread, ended


def _still_runs(fleet: Fleet, task: Task) -> bool:
    """Whether the fake still runs task, as its record of tasks says, asking nothing that would count as a poll."""
    return fleet.tasks()[task.upid]["status"] == "running"


def test_a_follow_whose_run_stops_between_polls_is_stopped_at_once_and_the_task_goes_on(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start("pve", 9010)
        stop = threading.Event()
        thread, ended = _following(client, task, stop)
        status = f"nodes/pve/tasks/{task.upid}/status"
        deadline = time.monotonic() + 30
        # Past the pauses that grow: from the fifth poll on each pause is a second long; stopped just after a poll.
        while len(fleet.sent("GET", status)) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        seen = len(fleet.sent("GET", status))
        while len(fleet.sent("GET", status)) == seen and time.monotonic() < deadline:
            time.sleep(0.01)
        stopped = time.monotonic()
        stop.set()
        thread.join(timeout=10)
        polls = len(fleet.sent("GET", status))
        time.sleep(1.5)
    assert not thread.is_alive()
    # Within a pause, never the whole of one: the wait is the event's, not a sleep.
    assert ended["at"] - stopped < 0.5, ended["at"] - stopped
    raised = ended.get("raised")
    assert isinstance(raised, Stopped) and raised.task == task, ended
    assert str(raised) == (
        "Proxmox's start task for VM 9010 still runs on Proxmox: the run stopped following it, so read the VM to know"
        " where it was left"
    )
    # Nothing more is asked once it is stopped, and the task goes on on Proxmox.
    assert len(fleet.sent("GET", status)) == polls >= 6
    assert _still_runs(fleet, task)


def test_a_follow_whose_run_stops_during_a_poll_lets_the_poll_end_and_asks_no_other(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start("pve", 9010)
        asked, release = threading.Event(), threading.Event()
        polled = client._get

        def held(path: str, *args, **kwargs):
            if path.endswith("/status"):
                asked.set()
                release.wait(20)
            return polled(path, *args, **kwargs)

        client._get = held
        stop = threading.Event()
        thread, ended = _following(client, task, stop)
        assert asked.wait(20)
        stop.set()
        time.sleep(0.3)
        # The poll in flight is never cut: follow() waits for its answer, then asks nothing more.
        assert thread.is_alive()
        release.set()
        thread.join(timeout=10)
    assert isinstance(ended.get("raised"), Stopped), ended
    assert len(fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/status")) == 1
    assert _still_runs(fleet, task)


def test_a_poll_that_fails_once_the_run_has_stopped_is_the_stop_and_one_that_fails_before_is_its_own(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start("pve", 9010)
        polled = client._get
        stop = threading.Event()

        def closed(path: str, *args, **kwargs):
            # The run stops while the poll is under way, and its client is closed under it, as a run's end may.
            if path.endswith("/status"):
                stop.set()
                raise Unreachable("the connection was closed under the poll")
            return polled(path, *args, **kwargs)

        client._get = closed
        with pytest.raises(Stopped) as stopped:
            client.follow(task, stop=stop)
        assert stopped.value.task == task and isinstance(stopped.value.__cause__, Unreachable)

        def cut(path: str, *args, **kwargs):
            raise Unreachable("the connection was cut")

        # The same failure while the run goes on is the poll's own.
        client._get = cut
        with pytest.raises(Unreachable):
            client.follow(task, stop=threading.Event())


def test_a_follow_whose_run_is_already_stopping_asks_nothing(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start("pve", 9010)
        stop = threading.Event()
        stop.set()
        with pytest.raises(Stopped):
            client.follow(task, stop=stop)
    assert fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/status") == []


def test_a_follow_not_stopped_follows_its_task_to_its_end(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=3)
    with fleet.client() as client:
        task = client.start("pve", 9010)
        status = client.follow(task, stop=threading.Event())
    assert status.ok and not status.running


def test_each_write_answers_a_task_of_its_kind_that_changes_the_vm_when_it_ends(fleet) -> None:
    with fleet.client() as client:
        _cloned(client)
        steps = [
            (lambda: client.resize("pve", 9020, "scsi0", "40G"), "resize"),
            (lambda: client.start("pve", 9020), "start"),
            (lambda: client.shutdown("pve", 9020, timeout=30, force_stop=True), "shutdown"),
            (lambda: client.start("pve", 9020), "start"),
            (lambda: client.stop("pve", 9020), "stop"),
            (lambda: client.to_template("pve", 9020), "template"),
        ]
        made = []
        for write, kind in steps:
            task = write()
            made.append(task)
            assert (task.kind, task.vmid) == (kind, 9020)
            client.follow(task)
            if kind == "resize":
                assert "size=40G" in client.config("pve", 9020).values["scsi0"]
            if kind in ("start", "shutdown", "stop"):
                assert client.current("pve", 9020).status == ("running" if kind == "start" else "stopped")
        assert client.config("pve", 9020).template
        deletion = client.delete("pve", 9020)
        assert (deletion.kind, deletion.vmid) == ("delete", 9020) and client.follow(deletion).ok
        with pytest.raises(Refused) as gone:
            client.config("pve", 9020)
    assert gone.value.status == 500 and "does not exist" in gone.value.reason
    assert fleet.sent("PUT", "nodes/pve/qemu/9020/resize") == [{"disk": "scsi0", "size": "40G"}]
    assert fleet.sent("POST", "nodes/pve/qemu/9020/status/shutdown") == [{"timeout": "30", "forceStop": "1"}]
    assert fleet.sent("POST", "nodes/pve/qemu/9020/status/start") == [{}, {}]
    assert fleet.sent("DELETE", "nodes/pve/qemu/9020") == [{"destroy-unreferenced-disks": "1", "purge": "1"}]
    assert {fleet.tasks()[task.upid]["owner"] for task in made} == {fleet.token.id}


def test_a_delete_of_a_vm_outside_the_pool_is_denied_and_it_stays(fleet) -> None:
    with fleet.client() as client, pytest.raises(Denied) as denied:
        client.delete("pve", 9012)
    assert denied.value.status == 403
    assert fleet.raw(9012)["name"] == "gamma" and fleet.writes() == [("DELETE", "nodes/pve/qemu/9012")]


# --------------------------------------------------------------------------- held before anything is sent ----


@pytest.mark.parametrize(
    ("write", "args"),
    [
        ("write_config", ("pve", 9010, {"net0": "virtio,bridge=vmbr1"}, "0" * 40)),
        ("write_config", ("pve", 9010, {"digest": "0" * 40}, "0" * 40)),
        ("write_config", ("pve", 9010, {"startup": "order=1"}, "0" * 40)),
        ("write_config", ("pve", 9010, {"delete": "cores"}, "0" * 40)),
        ("write_config", ("pve", 9010, {"delete": ["net0"]}, "0" * 40)),
        ("write_config", ("pve", 9010, {"tags": "a,b"}, "0" * 40)),
        ("write_config", ("pve", 9010, {"tags": ["a b"]}, "0" * 40)),
        ("write_config", ("pve", 9010, {"cores": True}, "0" * 40)),
        ("write_config", ("pve", 9010, {"sshkeys": b"key"}, "0" * 40)),
        ("write_config", ("pve", 9010, {}, "0" * 40)),
        ("write_config", ("pve", 9010, {"cores": 2}, "not a digest")),
        ("write_config", ("pve", 9010, {"cores": 2}, None)),
        ("clone", ("pve", 9000, 9020, "a name", ENGINE_POOL)),
        ("clone", ("pve", 9000, 9020, "delta", "../access")),
        ("clone", ("pve", 9000, 9020, "delta", ENGINE_POOL, False, "local-lvm")),
        ("clone", ("pve", 9000, 99, "delta", ENGINE_POOL)),
        ("resize", ("pve", 9010, "efidisk0", "40G")),
        ("resize", ("pve", 9010, "scsi0", "-1G")),
        ("resize", ("pve", 9010, "scsi0", "40G&delete=1")),
        ("shutdown", ("pve", 9010, -1)),
        ("delete", ("pve/../x", 9010)),
        ("delete", ("pve", 9010, "yes")),
        ("follow", ("UPID:pve:00000001:00000000:00000000:qmstart:1:root@pam:",)),
    ],
)
def test_a_write_that_is_not_one_proxmox_takes_as_the_engine_s_is_refused_before_anything_is_sent(
    fleet, write, args
) -> None:
    with fleet.client() as client, pytest.raises((ValueError, TypeError)):
        getattr(client, write)(*args)
    assert fleet.asked() == []


def test_a_deadline_is_a_number_of_seconds_above_0(fleet) -> None:
    task = answers.task("UPID:pve:00000001:00000000:00000000:qmstart:1:root@pam:", "pve", "start", 9010)
    with fleet.client() as client:
        for deadline in (0, -1, True, "5"):
            with pytest.raises(ValueError):
                client.follow(task, deadline=deadline)
        # What stops a follow is the run's threading.Event, Context.stopping: an asyncio.Event is never set from a
        # thread, and a flag is never waited on.
        for stop in (asyncio.Event(), True, "stop"):
            with pytest.raises(TypeError):
                client.follow(task, stop=stop)
    assert fleet.asked() == []
    with pytest.raises(answers.Malformed):
        answers.task(None, "pve", "start", 9010)
    with pytest.raises(ValueError):
        answers.task("UPID:pve:00000001:00000000:00000000:qmstart:1:root@pam:", "pve", "reboot", 9010)


# ------------------------------------------------------------------------------------------------- the rules ----


def test_a_configuration_s_keys_take_the_privileges_the_table_s_rows_hold_for_its_write_and_no_more() -> None:
    rows = {call.privilege for call in privileges.CALLS if call.name == "write_config"}
    assert set(CONFIG_KEYS.values()) == rows
    assert all(call.on == "vm" for call in privileges.CALLS if call.name == "write_config")


def test_every_row_of_the_table_names_a_method_of_the_client() -> None:
    named = {call.name for call in privileges.CALLS}
    assert named <= {name for name in dir(Proxmox) if not name.startswith("_")}, named - set(dir(Proxmox))
    writes = {"clone", "write_config", "resize", "start", "shutdown", "stop", "to_template", "delete"}
    assert writes <= named and not hasattr(Proxmox, "pool_add")


def _sends(call: ast.Call) -> tuple[str, str] | None:
    """The method and the path's last literal text of a call of _send, _request or a transport's request; None for
    any other call."""
    name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
    if name not in ("_send", "_request", "request") or len(call.args) < 2:
        return None
    method, path = call.args[0], call.args[1]
    if not (isinstance(method, ast.Constant) and isinstance(method.value, str)):
        return None
    if isinstance(path, ast.JoinedStr) and path.values and isinstance(path.values[-1], ast.Constant):
        return method.value, str(path.values[-1].value)
    if isinstance(path, ast.Constant) and isinstance(path.value, str):
        return method.value, path.value
    return method.value, ""


def _says_digest(function: ast.AST) -> bool:
    """Whether function puts digest in what it sends: a subscript assigned, a dict's key or a keyword."""
    for node in ast.walk(function):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "digest":
            if isinstance(node.ctx, ast.Store):
                return True
        if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "digest" for k in node.keys):
            return True
        if isinstance(node, ast.keyword) and node.arg == "digest":
            return True
    return False


def _config_puts_without_digest(source: str) -> tuple[list[str], list[str]]:
    """The functions of source that send a PUT to a configuration's path, and those of them that send no digest."""
    putting, without = [], []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        sent = [_sends(node) for node in ast.walk(function) if isinstance(node, ast.Call)]
        if any(found and found[0] == "PUT" and found[1].endswith("/config") for found in sent):
            putting.append(function.name)
            if not _says_digest(function):
                without.append(function.name)
    return putting, without


def test_every_method_that_sends_a_put_to_a_config_path_sends_digest() -> None:
    putting, without = _config_puts_without_digest(CLIENT.read_text())
    assert putting == ["write_config"] and without == []


@pytest.mark.parametrize(
    ("source", "putting", "without"),
    [
        ('def w(self, n, v, c):\n    self._send("PUT", f"nodes/{n}/qemu/{v}/config", v, form=c)', ["w"], ["w"]),
        (
            'def w(self, n, v, c, d):\n    c["digest"] = d\n'
            '    self._send("PUT", f"nodes/{n}/qemu/{v}/config", v, form=c)',
            ["w"],
            [],
        ),
        ('def w(self, n, v):\n    self._send("PUT", f"nodes/{n}/qemu/{v}/config", v, form={"digest": 1})', ["w"], []),
        ('def w(self, n, v):\n    self._send("POST", f"nodes/{n}/qemu/{v}/config", v)', [], []),
        ('def w(self, n, v):\n    self._send("PUT", f"nodes/{n}/qemu/{v}/resize", v)', [], []),
        ('def w(self, c):\n    self._transport.request("PUT", "nodes/pve/qemu/9010/config", form=c)', ["w"], ["w"]),
    ],
)
def test_the_scan_sees_a_put_to_a_config_path_and_its_digest(source, putting, without) -> None:
    assert _config_puts_without_digest(source) == (putting, without)


def test_the_words_the_client_tells_proxmox_s_refusals_apart_by_are_the_fake_s() -> None:
    assert REFUSED_STATUS == SAID["DIGEST_REFUSED_STATUS"] == SAID["LOCKED_STATUS"] == 500
    assert STALE.search(SAID["DIGEST_REFUSED"]) and not LOCK.search(SAID["DIGEST_REFUSED"])
    for lock in (SAID["CLONE_LOCK"], SAID["DELETION_LOCK"]):
        found = LOCK.search(SAID["LOCKED"].format(lock=lock))
        assert found and found.group(1) == lock and not STALE.search(SAID["LOCKED"].format(lock=lock))
    for words in (
        SAID["CLONE_EXISTS"].format(newid=9020),
        SAID["CLONE_LOCK_TIMEOUT"].format(newid=9020),
        SAID["CLONE_TAKEN"].format(kind="CT", newid=9020, node="pve"),
        SAID["CLONE_TAKEN"].format(kind="VM", newid=9020, node="pve2"),
    ):
        assert _taken(words, 9020) and not _taken(words, 902) and not _taken(words, 90200), words
    assert not _taken(SAID["LOCKED"].format(lock="clone"), 9020)


# ---------------------------------------------------------------------------------------------- the token ----


class _Answers:
    """A transport that answers each request with the next of answers, for what the fake does not say: Proxmox's words
    echoing the token's secret."""

    def __init__(self, *given: Answer) -> None:
        self.given = list(given)

    def request(self, *args, **kwargs) -> Answer:
        return self.given.pop(0)


VERSION = Answer(200, (), b'{"data": {"version": "9.2.21", "release": "9.2"}}', "OK")
UPID = "UPID:pve:00000001:00000000:00000000:qmstart:1:root@pam:"


def _echo(words: str) -> Answer:
    """A refusal whose status line's words are words."""
    return Answer(500, (), b'{"data": null}', words)


def _data(value) -> Answer:
    return Answer(200, (), json.dumps({"data": value}).encode(), "OK")


ECHOES = {
    "the header": f"PVEAPIToken={ENGINE_USER}!{ENGINE_TOKEN}={CANARY}",
    "the secret cut by a control character": f"{CANARY[:7]}\x07{CANARY[7:]} refused",
    "the secret where the words are cut": "x" * 180 + CANARY,
}
RAISED = {
    "a read's Refused": (lambda c: c.config("pve", 9010), lambda echo: [_echo(echo)], Refused),
    "a write's Refused": (
        lambda c: c.write_config("pve", 9010, {"cores": 2}, "0" * 40),
        lambda echo: [_echo(echo)],
        Refused,
    ),
    "DigestMismatch": (
        lambda c: c.write_config("pve", 9010, {"cores": 2}, "0" * 40),
        lambda echo: [_echo(f"checksum mismatch {echo}")],
        DigestMismatch,
    ),
    "Locked": (lambda c: c.start("pve", 9010), lambda echo: [_echo(f"VM is locked (clone) {echo}")], Locked),
    "Exists": (
        lambda c: c.clone("pve", 9000, 9020, "delta", ENGINE_POOL),
        lambda echo: [_echo(f"unable to create VM 9020: config file already exists {echo}")],
        Exists,
    ),
    "TaskFailed": (
        lambda c: c.follow(answers.task(UPID, "pve", "start", 9010)),
        lambda echo: [
            _data({"status": "stopped", "exitstatus": f"start failed: {echo}"}),
            _data([{"n": 1, "t": f"TASK ERROR: start failed: {echo}"}]),
        ],
        TaskFailed,
    ),
}


@pytest.mark.parametrize("echo", ECHOES.values(), ids=ECHOES.keys())
@pytest.mark.parametrize("raised", RAISED.values(), ids=RAISED.keys())
def test_no_error_says_the_token_s_secret_even_where_proxmox_s_words_echo_it(pki, caplog, capsys, echo, raised) -> None:
    """A host, or a proxy the computer's certificates trust, may put what it was sent in its words: the client strikes
    the secret from them before it says any, as they came and once made plain, and before it cuts them. The frames of
    the traceback hold the answer as it came, the peer's own words, and are not looked at: what is said is."""
    caplog.set_level(logging.DEBUG)
    call, given, kind = raised
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = _Answers(VERSION, *given(echo))
    with pytest.raises(kind) as error:
        call(client)
    said = _said(error.value, caplog, capsys, frames=False)
    if isinstance(error.value, TaskFailed):
        said += [error.value.error, *error.value.lines]
    if isinstance(error.value, Refused):
        said.append(error.value.reason)
    # No part of it either: no ten characters of it in a row, which a secret struck only once its words were cut would
    # leave.
    parts = {CANARY[at : at + 10] for at in range(len(CANARY) - 9)}
    assert not [text for text in said if any(part in text for part in parts)], said
    if kind in (Refused, TaskFailed) and echo == ECHOES["the header"]:
        assert MARK in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None
