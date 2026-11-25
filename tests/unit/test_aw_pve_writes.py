"""The Proxmox client's writes (lib/claude_vm/pve/client.py and answers.py; docs/architecture/engine.md §16.5).

Every write goes to the fake Proxmox (tests/molecule/fake_pve_api.py) through harness.fake_fleet with the engine's pool
and its one token, granted as init grants them and held to the engine's table of privileges (enforce): every request a
write sends is a call the table has a row for, answered only where the token holds that row's privilege, so a write the
token may not make is Denied by the fake, as Proxmox refuses it, never by a refusal planted for the test. The template
and alpha are the pool's members; beta and gamma are not.

A clone into the pool with a description is proved by the new VM's own configuration. A clone onto a VMID that is taken
is Exists, at the call or in its task in each of Proxmox's three sentences, and nothing is ever deleted at that VMID. A
configuration's write sends the digest of the read its Owned was made on, and takes none of its caller's, and a stale
one is DigestMismatch with nothing written; edit_config reads and applies again, three times at most. No write of an
Owned reaches another VM at its VMID (card G28): every other write reads the configuration first and holds its SMBIOS
UUID to the Owned's, Changed with nothing sent, a resize sending that read's digest. A conversion to a template is made
on a Building alone, a build VM of the asking run's own, and held to its UUID and digest as a deletion is; a VM of the
fleet, a template, another run's build VM and one at another clone's VMID are refused. A write to a VM a clone or a
deletion holds is Locked, and
tried once; so is one that meets its configuration's file lock, and a clone's task that meets its source's lock names
the source. A write whose answer is cut is the transport's Unknown, sent once. A task that fails raises with the last
lines of its log, made plain; one that ends with warnings is a success, its warnings' lines kept; one that runs past its
deadline raises then, and is not waited for longer, each poll held to what is left of it; one whose run stops before its
end is read is Stopped, between polls or during one, and goes on on Proxmox, unfollowed, and a poll that fails once the
run has stopped is Stopped only where the run's close made it fail (the transport's mark), its own failure, a refusal
among them, raised as itself otherwise. The log of a task that has
ended is read within what is left of the same deadline and never decides the outcome: where it cannot be read, a success
is a success with no lines and a failure TaskFailed with none, the run's stop among what cuts the read. A deletion sends
destroy-unreferenced-disks alone, and purge only where it is asked for. The rules with a test of their own: a PUT or a
POST to a configuration's path with no digest, or with one that is not a digest, is refused before it is sent, the path
read with its %-escapes decoded and sent as it was given, every one the fake was sent carried it (harness.fake_fleet's
end, for every test of the new engine, reading the path decoded too), and every method that sends one sends digest (a
syntax-tree test of the module); a configuration's write sends only the keys the table covers for it; both ways, every
row of the table is one the fake decided a request of the client's by and every call the client makes has its rows,
each method of the client driven against the fake under enforcement, its requests read from the fake's log and the rows
each was decided by from its decided.log; and no error of a write, nor of a read,
says the token's secret, even where Proxmox's words echo it.
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
from aw_kit import owned_as_recorded
from harness import (
    ENGINE_POOL,
    ENGINE_TOKEN,
    ENGINE_USER,
    FAKES,
    REPO,
    _pve,
    _pve_requests,
    digestless,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from test_aw_pve_agent import _holding
from test_aw_transport import _said, _Tunnel

from claude_vm.pve import answers, ownership, pin, privileges
from claude_vm.pve import record as block
from claude_vm.pve.client import (
    DEADLINES,
    EDITS,
    FILE,
    FILE_LOCK,
    LOCK,
    REFUSED_STATUS,
    STALE,
    TAKEN,
    Changed,
    Denied,
    DigestMismatch,
    Exists,
    Locked,
    Malformed,
    Proxmox,
    Refused,
    Stopped,
    Task,
    TaskFailed,
    TaskTimedOut,
    Token,
    TooOld,
    Unknown,
    Unverified,
    _file_locked,
    _taken,
)
from claude_vm.pve.ownership import Building, NotOwned, Owned
from claude_vm.secret import MARK, Secret
from claude_vm.vendors.transport import CLOSED, CUT, Answer, Closed, TimedOut, Unreachable, Untrusted

# The module's certificate authority, which signs the fake's certificate: cheap enough to make again on each worker
# (tests/unit/conftest.py).
EACH_WORKER = {"pki"}
CLIENT = REPO / "lib" / "claude_vm" / "pve" / "client.py"
# The SMBIOS UUID of a VM a test makes up, for an Owned no write reads (_vm).
MADE_UP = "0f0e0d0c-0b0a-4908-8706-050403020100"
# Another VM's SMBIOS UUID, put at an owned VMID as a VM cloned there after the read would carry its own.
OTHER = "9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c3"
# The asking run's job, as a build VM's record names it, and another run's.
JOB, ANOTHER_JOB = "2b3c4d5e6f708192", "0123456789abcdef"
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

    def read(self, vmid: int) -> answers.VmConfig:
        """A VM's configuration as today's token reads it, as the client's answers make one: so that the client asks
        nothing more of it."""
        return answers.config(self.raw(vmid), "pve", vmid, "net0")

    def owned(self, vmid: int) -> Owned:
        """The VM at vmid as a write takes it: an Owned, which ownership.owned alone makes, of its configuration now as
        today's token reads it, its record taken as one of the fleet's own (aw_kit's owned_as_recorded). Whether a
        member is the fleet's is the one check's (test_aw_ownership.py, test_aw_fleet.py): here, what each write
        sends."""
        return owned_as_recorded(self.read(vmid))

    def building(self, vmid: int, clone: Task, job: str = JOB, kind: str = "build") -> Building:
        """The VM at vmid, given a record of kind (build) of its own UUID and job, as a run that clones a build VM
        writes one, and judged from a read made now, no other VM carrying its UUID (twins=()): the Building
        pve/ownership.py's building makes of it for the run of JOB whose clone's task clone is. NotOwned where building
        refuses it."""
        config = self.read(vmid)
        fields = {"kind": kind, "uuid": config.identity, "job": job, "stage": "finished" if kind == "vm" else None}
        _pve(self.base, "PUT", f"nodes/pve/qemu/{vmid}/config", description=block.render("", fields))
        return ownership.building(ownership.Member.read(self.read(vmid), twins=()), job=JOB, clone=clone)

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


def _made_up(vmid: int) -> dict:
    """The configuration of a VM a test makes up, as Proxmox would answer it: its digest, its name, its SMBIOS UUID."""
    return {"digest": "0" * 40, "name": f"vm-{vmid}", "smbios1": f"uuid={MADE_UP}"}


def _vm(vmid: int, node: str = "pve") -> Owned:
    """An Owned of VMID vmid on node, for a write a test refuses before it is sent or answers itself: what it was owned
    on is made up (_made_up), which a test that answers a write's read answers again."""
    return owned_as_recorded(answers.config(_made_up(vmid), node, vmid, "net0"))


def _cloned(client: Proxmox, newid: int = 9020, name: str = "delta", **kwargs) -> Task:
    """A clone of the template onto newid in the pool, followed to its end."""
    task = client.clone("pve", 9000, newid, name, ENGINE_POOL, **kwargs)
    client.follow(task)
    return task


# --------------------------------------------------------------------------------------------- a clone ----


def test_a_clone_into_the_pool_with_a_description_is_proved_by_its_own_config(fleet) -> None:
    with fleet.client() as client:
        task = client.clone("pve", 9000, 9020, "delta", ENGINE_POOL, storage="local-lvm", description="aw: cloning")
        assert (task.node, task.kind, task.vmid, task.source) == ("pve", "clone", 9020, 9000)
        assert answers.UPID.fullmatch(task.upid)
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


def test_a_write_sends_the_digest_its_owned_was_made_on_and_a_stale_one_writes_nothing(fleet) -> None:
    """write_config takes no digest of its caller's: it sends the one of the read the Owned was made on, so a second
    write of the same Owned, after the first changed the configuration, is refused."""
    owned = fleet.owned(9010)
    with fleet.client() as client:
        assert client.write_config(owned, {"description": "one", "cores": 4}) is None
        with pytest.raises(DigestMismatch) as stale:
            client.write_config(owned, {"description": "two"})
        after = client.config("pve", 9010)
        with pytest.raises(TypeError):
            client.write_config(owned, {"description": "three"}, after.digest)
    assert stale.value.vmid == 9010 and "changed since it was read" in str(stale.value)
    assert (after.description, after.values["cores"]) == ("one", "4")
    assert [params["digest"] for params in fleet.sent("PUT", "nodes/pve/qemu/9010/config")] == [owned.digest] * 2


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
                assert client.edit_config(fleet.owned(9010), first) == {"description": "notes\nfirst"}
            return {"description": f"{config.description}\nsecond"}

        assert client.edit_config(fleet.owned(9010), second) == {"description": "notes\nfirst\nsecond"}
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
        client.edit_config(fleet.owned(9010), loses)
    assert len(applied) == EDITS == 3 and len(set(applied)) == 3
    assert len(fleet.sent("PUT", "nodes/pve/qemu/9010/config")) == 3
    assert fleet.raw(9010).get("description", "") != "never"


def test_an_edit_that_asks_for_nothing_writes_nothing(fleet) -> None:
    with fleet.client() as client:
        assert client.edit_config(fleet.owned(9010), lambda config: None) == {}
        assert client.edit_config(fleet.owned(9010), lambda config: {}) == {}
    assert fleet.writes() == []


def test_sshkeys_are_sent_percent_encoded_whole_and_read_back_as_proxmox_keeps_them(fleet) -> None:
    encoded = urllib.parse.quote(KEYS, safe="")
    with fleet.client() as client:
        client.edit_config(fleet.owned(9010), lambda config: {"sshkeys": KEYS, "ciuser": "dev", "ipconfig0": "ip=dhcp"})
        kept = client.config("pve", 9010).values["sshkeys"]
    (sent,) = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert sent["sshkeys"] == kept == encoded and urllib.parse.unquote(kept) == KEYS
    assert "%0A" in encoded and "%2B" in encoded and " " not in encoded


def test_tags_are_sent_comma_joined_and_read_back_in_either_spelling(fleet) -> None:
    with fleet.client() as client:
        client.edit_config(fleet.owned(9010), lambda config: {"tags": ["agent-warehouse", "aw-made"]})
        tags = client.config("pve", 9010).tags
    (sent,) = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert sent["tags"] == "agent-warehouse,aw-made" and fleet.raw(9010)["tags"] == "agent-warehouse;aw-made"
    assert tags == ("agent-warehouse", "aw-made")


def test_a_key_removed_is_sent_in_delete(fleet) -> None:
    with fleet.client() as client:
        client.edit_config(fleet.owned(9010), lambda config: {"delete": ["ciuser", "onboot"]})
    (sent,) = fleet.sent("PUT", "nodes/pve/qemu/9010/config")
    assert sent["delete"] == "ciuser,onboot" and not {"ciuser", "onboot"} & set(fleet.raw(9010))


# ------------------------------------------------------------------------------------- a VM Proxmox holds ----


def test_a_write_while_a_deletion_holds_the_vm_is_locked_and_tried_once(fleet) -> None:
    with fleet.client() as client:
        _cloned(client)
        _pve(fleet.base, "PUT", "fake/task-polls", polls=3)
        deletion = client.delete(fleet.owned(9020))
        with pytest.raises(Locked) as locked:
            client.edit_config(fleet.owned(9020), lambda config: {"description": "late"})
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
            client.write_config(fleet.owned(9020), {"description": "early"})
        _pve(fleet.base, "PUT", "fake/pool-member", vmid=9020, pool=ENGINE_POOL)
        with pytest.raises(Locked) as locked:
            client.write_config(fleet.owned(9020), {"description": "early"})
        assert (locked.value.vmid, locked.value.lock) == (9020, "clone") and "try again" in str(locked.value)
        _pve(fleet.base, "PUT", "fake/task-end", upid=clone.upid)
        assert client.follow(clone).ok
        client.edit_config(fleet.owned(9020), lambda config: {"description": "landed"})
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
        task = client.start(fleet.owned(9010))
        with pytest.raises(TaskFailed) as failed:
            client.follow(task)
    assert failed.value.task == task and failed.value.error == "start failed: no room"
    assert failed.value.lines == ("TASK ERROR: start failed: no room",)
    assert str(failed.value) == (
        "Proxmox's start task for VM 9010 failed: start failed: no room"
        " (its log ends: TASK ERROR: start failed: no room)"
    )
    assert fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/log") == [{"start": "0", "limit": "500"}]


WARNING = "iothread is only valid with virtio disk or virtio-scsi-single controller, ignoring"


def test_a_task_that_ended_with_warnings_is_a_success_its_warnings_kept_for_the_workflow_to_say(fleet) -> None:
    """Proxmox counts a task whose worker logged a warning and went on a success, as today's community.proxmox does
    (api_task_ok): a start of a person's own template with iothread on another controller logs one."""
    _pve(fleet.base, "PUT", "fake/task-warn", warning=f"{WARNING}\nsecond \x1b[31mone\x1b[0m", type="qmstart")
    with fleet.client() as client:
        _cloned(client)
        task = client.start(fleet.owned(9020))
        status = client.follow(task)
        assert client.current("pve", 9020).status == "running"
        plain = client.follow(client.stop(fleet.owned(9020)))
    assert status.ok and (status.exitstatus, status.warnings) == ("WARNINGS: 2", 2)
    assert status.lines == (f"WARN: {WARNING}", "WARN: second one", "TASK WARNINGS: 2")
    assert fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/log") == [{"start": "0", "limit": "500"}]
    # A task that ended OK has no warnings, and its log is not read.
    assert plain.ok and (plain.exitstatus, plain.warnings, plain.lines) == ("OK", 0, ())
    assert len([entry for entry in fleet.asked() if entry["path"].endswith("/log")]) == 1


def test_the_words_a_task_that_ended_with_warnings_is_told_by_are_the_fake_s() -> None:
    for count in (1, 2, 30):
        found = answers.WARNINGS.fullmatch(SAID["TASK_WARNINGS_STATUS"].format(count=count))
        assert found and int(found.group(1)) == count
    stopped = {"status": "stopped", "exitstatus": SAID["TASK_WARNINGS_STATUS"].format(count=1)}
    assert answers.task_status(stopped, "pve", UPID).ok and answers.task_status(stopped, "pve", UPID).warnings == 1
    for said in ("WARNINGS: 0", "WARNINGS: 1 and more", "WARNINGS: x", "warnings: 1", "TASK WARNINGS: 1", "WARN"):
        status = answers.task_status({"status": "stopped", "exitstatus": said}, "pve", UPID)
        assert not status.ok and status.warnings == 0, said
    running = answers.task_status({"status": "running", "exitstatus": "WARNINGS: 1"}, "pve", UPID)
    assert not running.ok and running.warnings == 0


def test_a_task_that_meets_its_vm_s_configuration_file_lock_is_locked(fleet) -> None:
    """Another task held the VM's configuration's file longer than Proxmox waits for it: the lock word is file. For a
    clone the same words with its new VMID say another run's clone holds it, Exists."""
    held = SAID["CLONE_LOCK_TIMEOUT"].format(newid=9020)
    _pve(fleet.base, "PUT", "fake/task-fail", error=held, type="qmstart")
    with fleet.client() as client:
        _cloned(client)
        with pytest.raises(Locked) as locked:
            client.follow(client.start(fleet.owned(9020)))
    assert (locked.value.vmid, locked.value.lock) == (9020, FILE) and "try again" in str(locked.value)


@pytest.mark.parametrize("lock", ["backup", "the file"])
def test_a_clone_s_task_that_meets_its_source_s_lock_is_locked_and_names_the_source(fleet, lock) -> None:
    error = SAID["LOCKED"].format(lock="backup") if lock == "backup" else SAID["CLONE_LOCK_TIMEOUT"].format(newid=9000)
    _pve(fleet.base, "PUT", "fake/task-fail", error=error, type="qmclone")
    with fleet.client() as client:
        task = client.clone("pve", 9000, 9020, "delta", ENGINE_POOL)
        with pytest.raises(Locked) as locked:
            client.follow(task)
    assert (locked.value.vmid, locked.value.lock) == (9000, "backup" if lock == "backup" else FILE)
    assert str(locked.value).startswith("VM 9000 is locked") and fleet.deletions() == []


def test_a_write_that_meets_its_configuration_s_file_lock_at_the_call_is_locked_and_a_clone_s_names_its_source(
    pki,
) -> None:
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    held = SAID["CLONE_LOCK_TIMEOUT"]
    client._transport = _Answers(
        VERSION,
        _echo(held.format(newid=9010)),
        _echo(held.format(newid=9011)),
        _echo(held.format(newid=9000)),
        _echo(held.format(newid=9020)),
    )
    with pytest.raises(Locked) as locked:
        client.write_config(_vm(9010), {"cores": 2})
    assert (locked.value.vmid, locked.value.lock) == (9010, FILE)
    # Another VM's file is not this VM's lock.
    with pytest.raises(Refused):
        client.write_config(_vm(9010), {"cores": 2})
    with pytest.raises(Locked) as source:
        client.clone("pve", 9000, 9020, "delta", ENGINE_POOL)
    assert (source.value.vmid, source.value.lock) == (9000, FILE)
    with pytest.raises(Exists):
        client.clone("pve", 9000, 9020, "delta", ENGINE_POOL)
    assert FILE_LOCK in TAKEN
    assert _file_locked(held.format(newid=9010), 9010) and not _file_locked(held.format(newid=9010), 901)


def test_a_task_past_its_deadline_raises_and_is_not_waited_for_longer(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start(fleet.owned(9010))
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
        task = client.start(fleet.owned(9010))
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
        "Proxmox's start task for VM 9010 was left before its end: the run stopped following it, and the task may"
        " still run on Proxmox, so read the VM to know where it was left"
    )
    # Nothing more is asked once it is stopped, and the task goes on on Proxmox.
    assert len(fleet.sent("GET", status)) == polls >= 6
    assert _still_runs(fleet, task)


def test_a_follow_whose_run_stops_during_a_poll_lets_the_poll_end_and_asks_no_other(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start(fleet.owned(9010))
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


class _StoppedAt:
    """A transport that answers the version, and every poll of a task's status as polled says (an Answer to return, or
    an error to raise), the run's stop set as it does when stop is given, as a run that stops during the poll."""

    def __init__(self, polled, stop: threading.Event | None) -> None:
        self.polled, self.stop = polled, stop
        self.polls = 0

    def request(self, method, path, deadline=None, **kwargs) -> Answer:
        if path == "version":
            return VERSION
        self.polls += 1
        if self.stop is not None:
            self.stop.set()
        return self.polled()


def _raising(error: BaseException):
    def polled():
        raise error

    return polled


# How a poll fails: through the run's own close of its client, the poll cut or its transport found closed (the
# transport's mark, closed), which once the run has stopped is the stop; or of itself, the network's, the transport's
# or the host's, which is the poll's own, whether the run has stopped or not (review/code/C22.md 5). Told by the mark
# alone, never the words: a marked failure in other words is the stop too, and one in CUT's words with no mark is the
# poll's own (review/code/B29.md 3).
POLL_FAILS = {
    "cut": (_raising(Unreachable(CUT, closed=True)), Unreachable, True),
    "found closed": (_raising(Closed(CLOSED)), Closed, True),
    "marked, in other words": (
        _raising(Unreachable("the connection was dropped: Broken pipe", closed=True)),
        Unreachable,
        True,
    ),
    "in CUT's words, unmarked": (_raising(Unreachable(CUT)), Unreachable, False),
    "dropped": (_raising(Unreachable("the connection was dropped: Connection reset by peer")), Unreachable, False),
    "timed out": (_raising(TimedOut("no answer within 30 s")), TimedOut, False),
    "Denied": (lambda: Answer(403, (), b"", "Forbidden"), Denied, False),
    "Unverified": (_raising(Untrusted("certificate has expired", 10)), Unverified, False),
    "Malformed": (lambda: Answer(200, (), b"no json", "OK"), Malformed, False),
}


@pytest.mark.parametrize("stopping", [True, False], ids=["the run stopping", "the run going on"])
@pytest.mark.parametrize(("polled", "raised", "by_close"), POLL_FAILS.values(), ids=POLL_FAILS.keys())
def test_a_poll_that_fails_once_the_run_has_stopped_is_the_stop_only_where_the_run_s_close_made_it_fail(
    pki, polled, raised, by_close, stopping
) -> None:
    """Once the run has stopped, a poll that its close made fail is Stopped, the task left before its end, its cause
    kept; any other failure is the poll's own and raised as itself, a refusal among them - Denied, Unverified,
    Malformed say what the host said, which Stopped would hide. While the run goes on, every failure is the poll's
    own. One poll either way."""
    stop = threading.Event()
    client = _logged(pki, _StoppedAt(polled, stop if stopping else None))
    task = answers.task(UPID, "pve", "start", 9010)
    if stopping and by_close:
        with pytest.raises(Stopped) as stopped:
            client.follow(task, stop=stop)
        assert stopped.value.task == task and isinstance(stopped.value.__cause__, raised)
    else:
        with pytest.raises(raised) as failed:
            client.follow(task, deadline=600, stop=stop)
        assert not isinstance(failed.value, Stopped)
    assert client._transport.polls == 1


def test_a_follow_whose_run_is_already_stopping_asks_nothing(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=100_000)
    with fleet.client() as client:
        task = client.start(fleet.owned(9010))
        stop = threading.Event()
        stop.set()
        with pytest.raises(Stopped):
            client.follow(task, stop=stop)
    assert fleet.sent("GET", f"nodes/pve/tasks/{task.upid}/status") == []


def test_a_follow_not_stopped_follows_its_task_to_its_end(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=3)
    with fleet.client() as client:
        task = client.start(fleet.owned(9010))
        status = client.follow(task, stop=threading.Event())
    assert status.ok and not status.running


def test_a_follow_whose_run_stops_and_closes_its_client_during_a_held_poll_is_stopped_and_asks_nothing_more(
    pki,
) -> None:
    """The meeting of follow()'s stop (card C22) with the closed transport (card B22): the run stops while a poll is
    held by the host and closes its client under it. The poll ends at its first error, within its read's limit (a
    second here), is never sent again on a new connection, and follow() says Stopped."""
    release = threading.Event()
    server = _holding(pki, release)
    url = f"https://127.0.0.1:{server.server_address[1]}"
    pinned = pin.kept(pki["ca"])
    client = Proxmox(url, pinned, Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = pin.transport(f"{url}/api2/json", pinned, read=1.0)
    task = answers.task(UPID, "pve", "start", 9010)
    status = f"/api2/json/nodes/pve/tasks/{task.upid}/status"

    def polls() -> list:
        return [one for one in server.asked if one.path == status]

    try:
        stop = threading.Event()
        thread, ended = _following(client, task, stop)
        deadline = time.monotonic() + 20
        while not polls() and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        client.close()
        closed = time.monotonic()
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
    assert isinstance(ended.get("raised"), Stopped), ended
    assert ended["at"] - closed < 1.0 + pin.CONNECT
    assert len(polls()) == 1


class _Unanswered:
    """A transport that answers the version and then no task's status: each poll waits what its call's deadline
    gives, or, where it gives none, five seconds (the transport's own default is 120), and is the transport's TimedOut,
    as the transport ends a GET that had no answer in time; or, at_once, it is TimedOut at once, as one whose read had
    no answer within its own wait, with the call's deadline still far off."""

    def __init__(self, at_once: bool = False) -> None:
        self.at_once = at_once
        self.deadlines: list[float | None] = []

    def request(self, method, path, deadline=None, **kwargs) -> Answer:
        if path == "version":
            return VERSION
        self.deadlines.append(deadline)
        if not self.at_once:
            time.sleep(deadline if deadline is not None else 5.0)
        raise TimedOut("no answer within 30 s")


def test_each_poll_of_a_task_is_held_to_what_is_left_of_its_deadline(pki) -> None:
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = waits = _Unanswered()
    task = answers.task(UPID, "pve", "start", 9010)
    began = time.monotonic()
    with pytest.raises(TaskTimedOut):
        client.follow(task, deadline=0.5)
    took = time.monotonic() - began
    assert 0.5 <= took < 1.5, took
    assert waits.deadlines and all(left is not None and 0 < left <= 0.5 for left in waits.deadlines), waits.deadlines


def test_a_poll_that_fails_with_time_left_raises_its_own_error_and_the_task_may_still_run(pki) -> None:
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = waits = _Unanswered(at_once=True)
    with pytest.raises(TimedOut):
        # The poll's read had no answer within its own wait, long before the task's deadline: the poll's error, not
        # TaskTimedOut, since the task may still run on Proxmox (follow's docstring says so).
        client.follow(answers.task(UPID, "pve", "start", 9010), deadline=600)
    assert len(waits.deadlines) == 1


class _Logged:
    """A transport that answers the version, then the task's status as ended (status), and its log as log says: log is
    called with the deadline its request was given and returns an Answer or raises; each request's path and deadline
    kept. at_end, where given, is called as the status is answered, as a run that stops just then."""

    def __init__(self, status: dict, log, at_end=None) -> None:
        self.status, self.log, self.at_end = status, log, at_end
        self.asked: list[tuple[str, float | None]] = []

    def request(self, method, path, deadline=None, **kwargs) -> Answer:
        self.asked.append((path, deadline))
        if path == "version":
            return VERSION
        if path.endswith("/status"):
            if self.at_end is not None:
                self.at_end()
            return _data(self.status)
        return self.log(deadline)

    def logs(self) -> list[float | None]:
        return [deadline for path, deadline in self.asked if path.endswith("/log")]


WARNED = {"status": "stopped", "exitstatus": "WARNINGS: 1"}
FAILED = {"status": "stopped", "exitstatus": "start failed: no room"}


def _raises(error: BaseException):
    def log(deadline):
        raise error

    return log


# How a log's read fails after the task has ended: each of the transport's errors a read may meet, and each of the
# client's (a 403, an answer that is no JSON, a 500).
LOG_FAILS = {
    "Unreachable": _raises(Unreachable("the connection was dropped")),
    "TimedOut": _raises(TimedOut("no answer within 30 s")),
    "Closed": _raises(Closed(CLOSED)),
    "Denied": lambda deadline: Answer(403, (), b"", "Forbidden"),
    "Malformed": lambda deadline: Answer(200, (), b"no json", "OK"),
    "Refused": lambda deadline: Answer(500, (), b'{"data": null}', "the node went away"),
}


def _logged(pki, logged: _Logged) -> Proxmox:
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = logged
    return client


@pytest.mark.parametrize("log", LOG_FAILS.values(), ids=LOG_FAILS.keys())
def test_a_warned_task_whose_log_cannot_be_read_is_still_a_success_with_no_lines(pki, log) -> None:
    """review/code/B22.md 2: the task's success is Proxmox's answer, already read; its log's lines are a courtesy, and
    a read of them that fails never turns the success into an error - least of all one that says the task may still
    run. The read is asked once."""
    logged = _Logged(WARNED, log)
    status = _logged(pki, logged).follow(answers.task(UPID, "pve", "start", 9010))
    assert status.ok and (status.warnings, status.lines) == (1, ())
    assert len(logged.logs()) == 1


@pytest.mark.parametrize("log", LOG_FAILS.values(), ids=LOG_FAILS.keys())
def test_a_failed_task_whose_log_cannot_be_read_is_task_failed_with_no_lines(pki, log) -> None:
    logged = _Logged(FAILED, log)
    task = answers.task(UPID, "pve", "start", 9010)
    with pytest.raises(TaskFailed) as failed:
        _logged(pki, logged).follow(task)
    assert (failed.value.task, failed.value.error, failed.value.lines) == (task, "start failed: no room", ())
    assert str(failed.value) == "Proxmox's start task for VM 9010 failed: start failed: no room"
    assert len(logged.logs()) == 1


@pytest.mark.parametrize("log", LOG_FAILS.values(), ids=LOG_FAILS.keys())
def test_a_log_whose_read_could_not_be_made_has_no_lines_as_one_with_none(pki, log) -> None:
    """No caller tells a log whose read could not be made - it failed, no time was left, or the run had stopped - from
    one that had no lines: each is (), never None."""
    task = answers.task(UPID, "pve", "start", 9010)
    client = _logged(pki, _Logged(WARNED, log))
    stopped = threading.Event()
    stopped.set()
    assert client._last_lines(task, time.monotonic() + 5, None) == ()
    assert client._last_lines(task, time.monotonic() - 1, None) == ()
    assert client._last_lines(task, time.monotonic() + 5, stopped) == ()
    empty = _logged(pki, _Logged(WARNED, lambda deadline: _data([])))
    assert empty._last_lines(task, time.monotonic() + 5, None) == ()


@pytest.mark.parametrize("status", [WARNED, FAILED], ids=["warned", "failed"])
def test_an_ended_task_s_log_is_read_within_what_is_left_of_follow_s_deadline(pki, status) -> None:
    """A host that holds the log's read: it is given what is left of the deadline follow() was given, and follow()
    ends within it, the task's end said with no lines."""

    def held(deadline):
        time.sleep(deadline if deadline is not None else 5.0)
        raise TimedOut("no answer within the call's deadline")

    logged = _Logged(status, held)
    client = _logged(pki, logged)
    began = time.monotonic()
    if status is WARNED:
        assert client.follow(answers.task(UPID, "pve", "start", 9010), deadline=0.5).lines == ()
    else:
        with pytest.raises(TaskFailed) as failed:
            client.follow(answers.task(UPID, "pve", "start", 9010), deadline=0.5)
        assert failed.value.lines == ()
    took = time.monotonic() - began
    assert took < 1.5, took
    (given,) = logged.logs()
    assert given is not None and 0 < given <= 0.5, given


def test_an_ended_task_s_log_is_not_read_once_its_deadline_has_passed(pki) -> None:
    """The last poll took what was left: no log is asked, and the success is said with no lines."""
    task = answers.task(UPID, "pve", "start", 9010)
    logged = _Logged(WARNED, LOG_FAILS["Unreachable"], at_end=lambda: time.sleep(0.6))
    assert _logged(pki, logged).follow(task, deadline=0.5).lines == ()
    assert logged.logs() == []


@pytest.mark.parametrize("when", ["as the task ends", "during the log's read"])
def test_a_run_that_stops_once_its_task_has_ended_has_its_success_or_its_failure_with_no_lines(pki, when) -> None:
    """The run stops just as the task ends, before its log is read, or while it is read, closing its client under the
    read: the task's end is Proxmox's answer, already read, so a success is still the success and a failure is still
    TaskFailed, each with no lines, never Stopped, which says a task may still run (the coordinator's ruling (a) on
    B26, card B28). Stopped before the read, nothing is asked."""
    task = answers.task(UPID, "pve", "start", 9010)
    for status in (WARNED, FAILED):
        stop = threading.Event()

        def closing(deadline, stop=stop):
            stop.set()
            raise Closed(CLOSED)

        at_end = stop.set if when == "as the task ends" else None
        logged = _Logged(status, closing, at_end=at_end)
        client = _logged(pki, logged)
        if status is WARNED:
            ended = client.follow(task, stop=stop)
            assert ended.ok and ended.lines == ()
        else:
            with pytest.raises(TaskFailed) as failed:
                client.follow(task, stop=stop)
            assert not isinstance(failed.value, Stopped) and failed.value.__cause__ is None
            assert (failed.value.task, failed.value.error, failed.value.lines) == (task, "start failed: no room", ())
            assert str(failed.value) == "Proxmox's start task for VM 9010 failed: start failed: no room"
        assert len(logged.logs()) == (0 if when == "as the task ends" else 1)


def test_a_run_that_stops_while_its_task_still_runs_is_stopped_and_reads_no_log(pki) -> None:
    """The other side of the same rule: where the last status follow() read says the task still runs, the stop leaves it
    before its end, which is Stopped, and no log is read."""
    task = answers.task(UPID, "pve", "start", 9010)
    stop = threading.Event()
    logged = _Logged({"status": "running"}, LOG_FAILS["Unreachable"], at_end=stop.set)
    with pytest.raises(Stopped) as stopped:
        _logged(pki, logged).follow(task, stop=stop)
    assert stopped.value.task == task and stopped.value.__cause__ is None
    assert logged.logs() == []


def test_each_write_answers_a_task_of_its_kind_that_changes_the_vm_when_it_ends(fleet) -> None:
    with fleet.client() as client:
        clone = _cloned(client)
        digest = fleet.raw(9020)["digest"]
        steps = [
            (lambda: client.resize(fleet.owned(9020), "scsi0", "40G"), "resize"),
            (lambda: client.start(fleet.owned(9020)), "start"),
            (lambda: client.shutdown(fleet.owned(9020), timeout=30, force_stop=True), "shutdown"),
            (lambda: client.start(fleet.owned(9020)), "start"),
            (lambda: client.stop(fleet.owned(9020)), "stop"),
            (lambda: client.to_template(fleet.building(9020, clone)), "template"),
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
        deletion = client.delete(fleet.owned(9020))
        assert (deletion.kind, deletion.vmid) == ("delete", 9020) and client.follow(deletion).ok
        with pytest.raises(Refused) as gone:
            client.config("pve", 9020)
    assert gone.value.status == 500 and "does not exist" in gone.value.reason
    # A resize sends the digest of the read it held the VM's identity to first.
    assert fleet.sent("PUT", "nodes/pve/qemu/9020/resize") == [{"disk": "scsi0", "size": "40G", "digest": digest}]
    assert fleet.sent("POST", "nodes/pve/qemu/9020/status/shutdown") == [{"timeout": "30", "forceStop": "1"}]
    assert fleet.sent("POST", "nodes/pve/qemu/9020/status/start") == [{}, {}]
    assert fleet.sent("DELETE", "nodes/pve/qemu/9020") == [{"destroy-unreferenced-disks": "1"}]
    assert {fleet.tasks()[task.upid]["owner"] for task in made} == {fleet.token.id}


def test_a_deletion_sends_destroy_unreferenced_disks_alone_and_purge_only_where_it_is_asked_for(fleet) -> None:
    """With purge, Proxmox also takes the VMID out of the host's backup jobs, replication and HA, and without it refuses
    to delete a VM a person put under HA or replication: the client sends it only where a caller asks for it, as
    today's role never does (roles/proxmox_vm/tasks/absent.yml)."""
    with fleet.client() as client:
        _cloned(client)
        _cloned(client, 9021, "epsilon")
        assert client.follow(client.delete(fleet.owned(9020))).ok
        assert client.follow(client.delete(fleet.owned(9021), purge=True)).ok
    assert fleet.sent("DELETE", "nodes/pve/qemu/9020") == [{"destroy-unreferenced-disks": "1"}]
    assert fleet.sent("DELETE", "nodes/pve/qemu/9021") == [{"destroy-unreferenced-disks": "1", "purge": "1"}]


def test_a_delete_of_a_vm_outside_the_pool_is_denied_and_it_stays(fleet) -> None:
    with fleet.client() as client, pytest.raises(Denied) as denied:
        client.delete(fleet.owned(9012))
    assert denied.value.status == 403
    assert fleet.raw(9012)["name"] == "gamma" and fleet.writes() == [("DELETE", "nodes/pve/qemu/9012")]


def test_a_deletion_is_held_to_the_read_its_vm_was_owned_on_and_sends_nothing_where_it_changed(fleet) -> None:
    """Proxmox's DELETE takes no digest, and a VMID can be another VM's between a read and the deletion (card G28):
    the configuration is read again first, its SMBIOS UUID and its digest held to the Owned's."""
    with fleet.client() as client:
        _cloned(client)
        owned = fleet.owned(9020)
        # A person's change since the read.
        _pve(fleet.base, "PUT", "nodes/pve/qemu/9020/config", cores=3)
        with pytest.raises(Changed) as changed:
            client.delete(owned)
        assert changed.value.vmid == 9020 and "changed since the read it was owned on" in str(changed.value)
        # Another VM at the VMID: the one owned deleted, and one made again there, with an SMBIOS UUID of its own.
        again = fleet.owned(9020)
        assert client.follow(client.delete(again)).ok
        _cloned(client, 9020, "other")
        for write in (lambda: client.delete(again), lambda: client.edit_config(again, lambda config: {"cores": 2})):
            with pytest.raises(Changed) as other:
                write()
            assert "is another VM than the one owned" in str(other.value)
        assert fleet.raw(9020)["name"] == "other"
        # Owned again from a read made now, it is deleted.
        assert client.follow(client.delete(fleet.owned(9020))).ok
    assert len(fleet.sent("DELETE", "nodes/pve/qemu/9020")) == 2
    assert fleet.sent("PUT", "nodes/pve/qemu/9020/config") == []


@pytest.mark.parametrize("write", ["write_config", "edit_config", "resize", "start", "shutdown", "stop", "delete"])
def test_no_write_of_an_owned_vm_reaches_another_vm_at_its_vmid(fleet, write) -> None:
    """Between the read alpha was owned on and a write, its VMID can be another VM's (card G28, review 51's P2): as root
    writes them here, another SMBIOS UUID and a person's notes. A write that reads the configuration first is Changed,
    with nothing sent but that read; write_config, which reads nothing, sends the Owned's own digest, which Proxmox
    refuses (DigestMismatch), nothing written. Either way the VM there is as it was."""
    owned = fleet.owned(9010)
    _pve(fleet.base, "PUT", "nodes/pve/qemu/9010/config", smbios1=f"uuid={OTHER}", description="a person's notes")
    there = fleet.raw(9010)
    call = {
        "write_config": lambda client: client.write_config(owned, {"description": "written over"}),
        "edit_config": lambda client: client.edit_config(owned, lambda config: {"description": "written over"}),
        "resize": lambda client: client.resize(owned, "scsi0", "+1G"),
        "start": lambda client: client.start(owned),
        "shutdown": lambda client: client.shutdown(owned, timeout=30, force_stop=True),
        "stop": lambda client: client.stop(owned),
        "delete": lambda client: client.delete(owned),
    }[write]
    with fleet.client() as client:
        with pytest.raises(DigestMismatch if write == "write_config" else Changed) as refused:
            call(client)
    assert refused.value.vmid == 9010
    if write == "write_config":
        assert fleet.writes() == [("PUT", "nodes/pve/qemu/9010/config")]
    else:
        assert "is another VM than the one owned" in str(refused.value)
        assert fleet.writes() == []
    assert fleet.raw(9010) == there


def test_only_a_build_vm_of_the_run_s_own_is_made_a_template(fleet) -> None:
    """A conversion to a template cannot be undone, and is made on a Building alone, which pve/ownership.py's building
    makes from a fresh read of a VM whose record is of kind build, of the asking run's own job, at the VMID its own
    clone made (card G28, review 51's P4): a VM of the fleet, by a bare VMID or as an Owned, is refused before anything
    is sent; so are a template, a build VM of another run's job and one at another clone's VMID. Its configuration is
    read again first and held to the Building's, as a deletion's is."""
    with fleet.client() as client:
        clone = _cloned(client)
        other = _cloned(client, 9021, "epsilon")
        before = len(fleet.asked())
        # A VM of the fleet, by a bare VMID or as an Owned.
        for refused in (lambda: client.to_template("pve", 9010), lambda: client.to_template(fleet.owned(9010))):
            with pytest.raises(TypeError):
                refused()
        assert fleet.asked()[before:] == []
        # The template, another run's build VM, a VM of the fleet's record, and a build VM at another clone's VMID.
        with pytest.raises(NotOwned, match="is a template already"):
            ownership.building(ownership.Member.read(fleet.read(9000), twins=()), job=JOB, clone=clone)
        with pytest.raises(NotOwned, match="the build VM of another run's job"):
            fleet.building(9020, clone, job=ANOTHER_JOB)
        with pytest.raises(NotOwned, match="a VM of the fleet, which is never made a template"):
            fleet.building(9020, clone, kind="vm")
        with pytest.raises(NotOwned, match="not the VM the asking run's own clone made"):
            fleet.building(9020, other)
        # Changed since it was judged, or another VM at its VMID: nothing sent but the read.
        made = fleet.building(9020, clone)
        _pve(fleet.base, "PUT", "nodes/pve/qemu/9020/config", cores=3)
        with pytest.raises(Changed, match="changed since the read it was judged a build VM on"):
            client.to_template(made)
        made = fleet.building(9020, clone)
        _pve(fleet.base, "PUT", "nodes/pve/qemu/9020/config", smbios1=f"uuid={OTHER}")
        with pytest.raises(Changed, match="is another VM than the build VM"):
            client.to_template(made)
        assert fleet.writes() == [("POST", "nodes/pve/qemu/9000/clone")] * 2
        assert not client.config("pve", 9020).template
        # The run's own build VM, judged now, is made a template.
        assert client.follow(client.to_template(fleet.building(9021, other))).ok
        assert client.config("pve", 9021).template and not client.config("pve", 9010).template
    assert fleet.writes()[2:] == [("POST", "nodes/pve/qemu/9021/template")]


@pytest.mark.parametrize(
    "write", ["write_config", "edit_config", "resize", "start", "shutdown", "stop", "delete", "to_template"]
)
def test_a_vm_is_written_to_only_as_an_owned_and_anything_else_sends_nothing(fleet, write) -> None:
    rest = {
        "write_config": ({"cores": 2},),
        "edit_config": (lambda config: {"cores": 2},),
        "resize": ("scsi0", "+1G"),
    }.get(write, ())
    # A conversion to a template takes a Building alone (pve/ownership.py's building), an Owned among what it refuses.
    says = "made a template only as a Building" if write == "to_template" else "only as an Owned"

    @dataclasses.dataclass(frozen=True)
    class Like:
        node: str
        vmid: int
        uuid: str
        digest: str

    with fleet.client() as client:
        read = client.config("pve", 9010)
        before = len(fleet.asked())
        like = Like("pve", 9010, read.identity, read.digest)
        for given in (read, like, 9010, ("pve", 9010)):
            with pytest.raises(TypeError, match=says):
                getattr(client, write)(given, *rest)
        assert fleet.asked()[before:] == []


# --------------------------------------------------------------------------- held before anything is sent ----


@pytest.mark.parametrize(
    ("write", "args"),
    [
        ("write_config", (_vm(9010), {"net0": "virtio,bridge=vmbr1"})),
        ("write_config", (_vm(9010), {"digest": "0" * 40})),
        ("write_config", (_vm(9010), {"startup": "order=1"})),
        ("write_config", (_vm(9010), {"delete": "cores"})),
        ("write_config", (_vm(9010), {"delete": ["net0"]})),
        ("write_config", (_vm(9010), {"tags": "a,b"})),
        ("write_config", (_vm(9010), {"tags": ["a b"]})),
        ("write_config", (_vm(9010), {"cores": True})),
        ("write_config", (_vm(9010), {"sshkeys": b"key"})),
        ("write_config", (_vm(9010), {})),
        # A digest of the caller's: write_config sends its Owned's alone (card G28).
        ("write_config", (_vm(9010), {"cores": 2}, "0" * 40)),
        ("clone", ("pve", 9000, 9020, "a name", ENGINE_POOL)),
        ("clone", ("pve", 9000, 9020, "delta", "../access")),
        ("clone", ("pve", 9000, 9020, "delta", ENGINE_POOL, False, "local-lvm")),
        ("clone", ("pve", 9000, 99, "delta", ENGINE_POOL)),
        ("resize", (_vm(9010), "efidisk0", "40G")),
        ("resize", (_vm(9010), "scsi0", "-1G")),
        ("resize", (_vm(9010), "scsi0", "40G&delete=1")),
        ("shutdown", (_vm(9010), -1)),
        ("delete", (_vm(9010, node="pve/../x"),)),
        ("delete", (_vm(9010), "yes")),
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
    with pytest.raises(ValueError):
        answers.task("UPID:pve:00000001:00000000:00000000:qmstart:1:root@pam:", "pve", "start", 9010, source=9000)


SLASHED = [
    "UPID:pve:00000001:00000000:00000000:qm/x:1:root@pam:",
    "UPID:pve:00000001:00000000:00000000:qmstart:1/../x:root@pam:",
    "UPID:pve:00000001:00000000:00000000:qmstart:1:root/../x@pam:",
]


@pytest.mark.parametrize("upid", SLASHED, ids=["its type", "its ID", "its user"])
def test_a_write_answered_with_a_upid_that_holds_a_slash_is_malformed(pki, upid) -> None:
    """A write's answer is a UPID the client puts back into two paths, a task's status and its log: one with a / in a
    part of its own would reach another path."""
    with pytest.raises(Malformed):
        answers.task(upid, "pve", "start", 9010)
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = _Answers(VERSION, CONFIG, _data(upid))
    with pytest.raises(Malformed):
        client.start(_vm(9010))
    assert answers.task(upid.replace("/", "-"), "pve", "start", 9010).upid == upid.replace("/", "-")


# ------------------------------------------------------------------------------------------------- the rules ----


# The client's public methods that send no request of rows named for them: follow polls a task through task_status and
# task_log, edit_config reads and writes by config's and write_config's rows, and close sends nothing.
APART = {"follow", "edit_config", "close"}
# The writes that read the VM's configuration first, by config's rows, and hold the VM at its VMID to what it was
# judged on (card G28): every one but write_config, which sends its Owned's digest, and a clone, which makes a VM where
# none is.
LOOK_FIRST = {"resize", "start", "shutdown", "stop", "to_template", "delete"}


def _sent_by_each_method(fleet: Fleet) -> dict[str, list[dict]]:
    """Each method of the client that has rows, called against the fake under enforcement as a workflow calls it, the
    version first and every write of a configuration with the keys of one of the write's rows: what each sent, as the
    fake logged it, by the method's name. A task is read that another made, whose owner the engine's token is not, so
    that its rows are checked: its owner needs nothing to read it."""
    engine_grant(fleet.base, "/nodes/pve", "Sys.Audit")
    engine_grant(fleet.base, "/sdn/zones/localnetwork/vmbr0", ",".join(sorted(privileges.needed("bridge"))))
    _pve(fleet.base, "PUT", "fake/agent-ready-after", polls=0)
    others = _pve(fleet.base, "POST", "nodes/pve/qemu/9012/status/start")["data"]
    sent: dict[str, list[dict]] = {}
    with fleet.client() as client:
        made: list[Task] = []
        steps = [
            ("version", lambda: client.version()),
            ("resources", lambda: [client.resources(kind) for kind in ("vm", "storage", "node")]),
            ("pool", lambda: client.pool(ENGINE_POOL)),
            ("config", lambda: client.config("pve", 9010)),
            ("current", lambda: client.current("pve", 9010)),
            ("next_id", lambda: client.next_id()),
            ("node_status", lambda: client.node_status("pve")),
            ("storages", lambda: client.storages("pve")),
            ("bridges", lambda: client.bridges("pve")),
            ("permissions", lambda: client.permissions(f"/pool/{ENGINE_POOL}")),
            ("read", lambda: client.read("nodes/pve/qemu/9010/rrddata", {"timeframe": "day"})),
            ("read", lambda: client.read("nodes/pve/qemu/9010/pending")),
            ("read", lambda: client.read("nodes/pve/storage/local-lvm/content")),
            ("agent_interfaces", lambda: client.agent_interfaces("pve", 9010)),
            ("clone", lambda: made.append(client.clone("pve", 9000, 9020, "delta", ENGINE_POOL))),
            ("task_status", lambda: [client.task_status("pve", upid) for upid in (made[0].upid, others)]),
            ("task_log", lambda: [client.task_log("pve", upid) for upid in (made[0].upid, others)]),
            *[
                (
                    "write_config",
                    lambda changes=changes: client.write_config(fleet.owned(9020), changes),
                )
                for changes in (
                    {"description": "the record", "tags": ["agent-warehouse"]},
                    {"ciuser": "dev", "sshkeys": KEYS},
                    {"nameserver": "192.0.2.53"},
                    {"cores": 2},
                    {"memory": 2048, "delete": ["balloon"]},
                )
            ],
            ("resize", lambda: client.resize(fleet.owned(9020), "scsi0", "+1G")),
            ("start", lambda: client.start(fleet.owned(9020))),
            ("shutdown", lambda: client.shutdown(fleet.owned(9020), timeout=30, force_stop=True)),
            ("start", lambda: client.start(fleet.owned(9020))),
            ("stop", lambda: client.stop(fleet.owned(9020))),
            ("to_template", lambda: client.to_template(fleet.building(9020, made[0]))),
            ("delete", lambda: client.delete(fleet.owned(9020))),
        ]
        for name, step in steps:
            before = len(fleet.asked())
            step()
            sent.setdefault(name, []).extend(fleet.asked()[before:])
    return sent


def _decided(fleet: Fleet) -> dict[int, set[str]]:
    """Each row of the table, by its index in CALLS, that the fake decided a request of the engine's token by, and how:
    held on a path, read by a listing for its entries, or needing nothing (its decided.log)."""
    log = fleet.directory / "pve" / "decided.log"
    decided: dict[int, set[str]] = {}
    for line in log.read_text().splitlines() if log.exists() else ():
        entry = json.loads(line)
        decided.setdefault(entry["row"], set()).add(entry["by"])
        rows = {call for call, _ in privileges.for_request(entry["method"], entry["path"])}
        assert privileges.CALLS[entry["row"]] in rows, entry
    return decided


def test_every_row_is_a_call_the_client_makes_and_every_call_it_makes_has_its_rows(fleet) -> None:
    """Both ways: the rows' names are the public methods of the client that send a request, those named apart aside;
    every request each method sent under enforcement is a call whose rows bear its name; and every row is one the fake
    decided a request the client sent by - held on the path the request names, read by its listing for its entries, or
    needing nothing - a configuration's row by a write that sent one of its keys. A row no check reads, on a route the
    client sends or not, would widen the role init grants from the table, unseen; a call with no row fails its own test
    at the fleet's end."""
    methods = {name for name, member in vars(Proxmox).items() if callable(member) and not name.startswith("_")}
    named = {call.name for call in privileges.CALLS}
    assert APART <= methods and named == methods - APART, named ^ (methods - APART)
    sent = _sent_by_each_method(fleet)
    assert set(sent) == named
    for name, requests in sent.items():
        assert requests, name
        # A write that holds the VM at its VMID reads its configuration first, by config's rows (card G28), then sends
        # its own.
        looks = requests[:1] if name in LOOK_FIRST else []
        assert [(request["method"], request["path"].rsplit("/", 1)[-1]) for request in looks] == (
            [("GET", "config")] if name in LOOK_FIRST else []
        )
        for request in requests:
            rows = privileges.for_request(request["method"], request["path"].removeprefix("/api2/json"))
            own = "config" if request in looks else name
            assert rows and {call.name for call, _ in rows} == {own}, (name, request["method"], request["path"])
    decided = _decided(fleet)
    for row, call in enumerate(privileges.CALLS):
        assert row in decided, call
        assert decided[row] <= ({"path", "listing"} if call.privileges else {"nothing"}), (call, decided[row])


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
    """The functions of source that send a PUT or a POST to a configuration's path, and those of them that send no
    digest: a second line, behind the client's own refusal and the fleet's end, both of which read what is sent."""
    putting, without = [], []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        sent = [_sends(node) for node in ast.walk(function) if isinstance(node, ast.Call)]
        # The path's text read as Proxmox reads a path, its %-escapes decoded (card B28).
        configs = [found for found in sent if found and found[0] in ("PUT", "POST")]
        if any(urllib.parse.unquote(found[1]).rstrip("/").endswith("/config") for found in configs):
            putting.append(function.name)
            if not _says_digest(function):
                without.append(function.name)
    return putting, without


def test_every_method_that_sends_a_put_or_a_post_to_a_config_path_sends_digest() -> None:
    putting, without = _config_puts_without_digest(CLIENT.read_text())
    assert putting == ["_write"] and without == []


@pytest.mark.parametrize("method", ["PUT", "POST"])
@pytest.mark.parametrize(
    "path",
    [
        "nodes/pve/qemu/9010/config",
        "nodes/pve/qemu/9010/config/",
        # A configuration's path as Proxmox reads one, its %-escapes decoded (review/code/B22.md 7, card B28).
        "nodes/pve/qemu/9010/confi%67",
        "nodes/pve/qemu/9010/%63%6F%6e%66%69%67/",
        # Decoded whole, an escaped / among them: the reading that refuses more.
        "nodes/pve/qemu/9010%2Fconfig",
    ],
)
def test_a_write_to_a_configuration_s_path_with_no_digest_is_refused_before_it_is_sent(fleet, method, path) -> None:
    """Where every request passes, whatever method made it and however its path was built or spelled: Proxmox would
    write it whatever the configuration had become since it was read. Nothing reaches the fake."""
    with fleet.client() as client:
        with pytest.raises(ValueError, match="sends the digest"):
            client._send(method, path, 9010, form={"description": "late"})
        with pytest.raises(ValueError, match="sends the digest"):
            client._request(method, path, form=None)
        # A digest that is not one, an empty one above all, which Proxmox reads as none (review/code/B22.md 7).
        for digest in ("", " ", "0" * 39, "0" * 40 + "\n", "G" * 40, 0, None, Secret("0" * 40)):
            with pytest.raises(ValueError, match="sends the digest"):
                client._send(method, path, 9010, form={"description": "late", "digest": digest})
        assert client.config("pve", 9010).description != "late"
    assert fleet.writes() == []


def test_every_configuration_write_the_client_sent_the_fake_carried_its_digest_at_the_fleet_s_end(
    tmp_path, pki
) -> None:
    """harness.fake_fleet(engine=True) reads, at its end, what the fake was sent: a write past the client's own refusal,
    sent with the client's user agent and no digest, fails the test then."""
    with pytest.raises(AssertionError, match="no digest"):
        for base in fake_fleet(tmp_path, engine=True, certificate=(pki["cert"], pki["key"])):
            token_id, secret = engine_token(tmp_path)
            with Proxmox(base, pin.kept(pki["ca"]), Token(token_id, Secret(secret))) as client:
                header = {"Authorization": (f"PVEAPIToken={token_id}=", Secret(secret))}
                client._transport.request("PUT", "nodes/pve/qemu/9010/config", headers=header, form={"cores": "3"})
                # A configuration's path %-escaped is one too, as the fake's routing and Proxmox read it (card B28).
                for method in ("PUT", "POST"):
                    client._transport.request(
                        method, "nodes/pve/qemu/9010/confi%67", headers=header, form={"cores": "3"}
                    )
                # A write that is no configuration's is not one of them.
                client._transport.request("POST", "nodes/pve/qemu/9012/status/start", headers=header)
    assert [(bare["method"], bare["path"], bare["params"]) for bare in digestless(tmp_path)] == [
        ("PUT", "/api2/json/nodes/pve/qemu/9010/config", {"cores": "3"}),
        ("PUT", "/api2/json/nodes/pve/qemu/9010/confi%67", {"cores": "3"}),
        ("POST", "/api2/json/nodes/pve/qemu/9010/confi%67", {"cores": "3"}),
    ]


def test_a_path_with_a_percent_is_sent_as_it_was_given_and_a_write_there_with_its_digest_is_taken(fleet) -> None:
    """The digest rule reads the path decoded, and only reads it: a path goes as it was given, never decoded and never
    escaped again, so a % is no refusal of its own (card B28). The fake routes it as Proxmox does."""
    with fleet.client() as client:
        digest = client.config("pve", 9010).digest
        client._send("PUT", "nodes/pve/qemu/9010/confi%67", 9010, form={"description": "escaped", "digest": digest})
        assert client.config("pve", 9010).description == "escaped"
    assert fleet.writes() == [("PUT", "nodes/pve/qemu/9010/confi%67")]


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
        ('def w(self, n, v):\n    self._send("POST", f"nodes/{n}/qemu/{v}/config", v)', ["w"], ["w"]),
        (
            'def w(self, n, v, d):\n    self._send("POST", f"nodes/{n}/qemu/{v}/config", v, form={"digest": d})',
            ["w"],
            [],
        ),
        ('def w(self, n, v):\n    self._send("PUT", f"nodes/{n}/qemu/{v}/resize", v)', [], []),
        ('def w(self, c):\n    self._transport.request("PUT", "nodes/pve/qemu/9010/config", form=c)', ["w"], ["w"]),
        ('def w(self, n, v):\n    self._send("POST", f"nodes/{n}/qemu/{v}/confi%67", v)', ["w"], ["w"]),
        ('def w(self, n, v):\n    self._send("PUT", f"nodes/{n}/qemu/{v}/config/", v, form={"digest": 1})', ["w"], []),
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


# A made-up VM's configuration (_made_up), as a write that holds the VM at its VMID reads it first: _vm's own.
CONFIG = _data(_made_up(9010))


ECHOES = {
    "the header": f"PVEAPIToken={ENGINE_USER}!{ENGINE_TOKEN}={CANARY}",
    "the secret cut by a control character": f"{CANARY[:7]}\x07{CANARY[7:]} refused",
    "the secret where the words are cut": "x" * 180 + CANARY,
}
RAISED = {
    "a read's Refused": (lambda c: c.config("pve", 9010), lambda echo: [VERSION, _echo(echo)], Refused),
    "a write's Refused": (
        lambda c: c.write_config(_vm(9010), {"cores": 2}),
        lambda echo: [VERSION, _echo(echo)],
        Refused,
    ),
    "DigestMismatch": (
        lambda c: c.write_config(_vm(9010), {"cores": 2}),
        lambda echo: [VERSION, _echo(f"checksum mismatch {echo}")],
        DigestMismatch,
    ),
    "Locked": (
        lambda c: c.start(_vm(9010)),
        lambda echo: [VERSION, CONFIG, _echo(f"VM is locked (clone) {echo}")],
        Locked,
    ),
    "Exists": (
        lambda c: c.clone("pve", 9000, 9020, "delta", ENGINE_POOL),
        lambda echo: [VERSION, _echo(f"unable to create VM 9020: config file already exists {echo}")],
        Exists,
    ),
    "TaskFailed": (
        lambda c: c.follow(answers.task(UPID, "pve", "start", 9010)),
        lambda echo: [
            VERSION,
            _data({"status": "stopped", "exitstatus": f"start failed: {echo}"}),
            _data([{"n": 1, "t": f"TASK ERROR: start failed: {echo}"}]),
        ],
        TaskFailed,
    ),
    "TooOld": (lambda c: c.version(), lambda echo: [_data({"version": f"8.2 {echo}", "release": "8.2"})], TooOld),
    "the version's Malformed": (
        lambda c: c.version(),
        lambda echo: [_data({"version": f"nine {echo}", "release": "9.2"})],
        Malformed,
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
    client._transport = _Answers(*given(echo))
    with pytest.raises(kind) as error:
        call(client)
    said = _said(error.value, caplog, capsys, frames=False)
    if isinstance(error.value, TaskFailed):
        said += [error.value.error, *error.value.lines]
    if isinstance(error.value, Refused):
        said.append(error.value.reason)
    if isinstance(error.value, TooOld):
        said.append(error.value.version)
    # No part of it either: no ten characters of it in a row, which a secret struck only once its words were cut would
    # leave.
    parts = {CANARY[at : at + 10] for at in range(len(CANARY) - 9)}
    assert not [text for text in said if any(part in text for part in parts)], said
    if kind in (Refused, TaskFailed) and echo == ECHOES["the header"]:
        assert MARK in str(error.value)
    assert error.value.__cause__ is None and error.value.__context__ is None
