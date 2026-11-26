"""A VM deleted by the new engine: lib/claude_vm/engine/destroy.py, destroy's handler, and
lib/claude_vm/engine/confirm.py, the one validator of a confirmation by names (card G5; docs/architecture/engine.md
§16.4).

Without the fake: the names confirmed as a set, each with - for _, and the name that differs said; the question, today's
block that names the VMs, each made one line, their disks and the host; what takes it (yes, today's true, in any case;
under destroy_confirm: names, the names) and that anything else does not; the override's rule, pve/ownership.py's
`unrecorded`, which lifts the refusal of a member with no record and no other (the invariant: never a template, never a
VM whose record names another UUID, never one with no SMBIOS UUID or a twin, whatever is given); and a scan that only
engine/destroy.py asks for it in lib/.

On a whole fleet folder (tests/unit/fleet_fixture.py) against the fake Proxmox (tests/molecule/fake_pve_api.py), the
engine's token held to its table of privileges, alpha and beta running and gamma stopped, each a member of the pool with
a record of its own. Carried from tests/unit/test_release.py, which stays as it is: an unrecorded member refused, and
deleted under the override; a template never deleted; a name two VMs carry refused before anything is started; an
unconfirmed deletion, with no terminal and at one, nothing deleted; one VM of two failing, its exit status and both
lines; the question naming the VMs and, with more than one host, the host. From tests/unit/test_proxmox_vm_absent.py:
the disks deleted with the VM (destroy-unreferenced-disks, no purge) and the product's known-hosts file left as it is.
From tests/unit/test_destroy_live.py: the declined deletion said in today's words, and a VM Proxmox would not delete
said not deleted while the other is. Its own: `aw destroy alpha --confirm=alpha` deleting the VM and its disks, the stop
before; `--confirm` with a name that differs refused, naming it, in the command's own process too; the names typed under
destroy_confirm: names; the refusal while destroy_save_sessions or destroy_release is on, before anything is sent; the
refusal while a member of the pool cannot be read; a VM outside the pool refused, naming the pool; a stop that met
Proxmox's file lock sent once more; a VM owned again after its stop, whose configuration the stop changed, and held to
the VM confirmed; and a Ctrl-C during the deletion, which finishes it and says so.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import os
import signal
import subprocess
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from aw_kit import aw_at_a_terminal, run_aw
from fleet_fixture import whole_fleet
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _pve, _pve_requests, _pve_vm, fake_fleet
from pki import proxmox_pki

from claude_vm import proxmox
from claude_vm.engine import confirm, destroy, fleet
from claude_vm.engine.record import Record
from claude_vm.fleet import hosts, paths
from claude_vm.hosts.model import Request
from claude_vm.pve import ownership, pin
from claude_vm.pve import record as block
from claude_vm.pve.client import Proxmox
from claude_vm.schema.words import EXITS, VERBS

DESTROY = REPO / "lib" / "claude_vm" / "engine" / "destroy.py"
ALPHA, BETA, GAMMA = 9010, 9011, 9012
TOKEN = f"{ENGINE_USER}!{ENGINE_TOKEN}"
# The two settings whose work cards G13 and G14 build, off for a run of this card's.
OFF = ("--set", "destroy_save_sessions=false", "--set", "destroy_release=false")
UNRECORDED = ("--set", "destroy_allow_unrecorded=true")
FILE_LOCK = "can't lock file '/var/lock/qemu-server/lock-{vmid}.conf' - got timeout"
UUID = "9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c4"
# A window wide enough that no line of the run's is wrapped: (columns, rows).
WINDOW = (160, 50)


# ---------------------------------------------------------------- the names confirmed ----


def test_the_names_confirmed_are_a_set_each_with_a_dash_for_an_underscore() -> None:
    assert confirm.names("beta,my_vm", ["my-vm", "beta"]) == ""
    assert confirm.names("alpha, beta", ["beta", "alpha"]) == ""
    assert confirm.names("alpha beta,", ["alpha", "beta"]) == ""
    assert confirm.names("alpha,alpha", ["alpha"]) == ""


def test_a_name_that_differs_is_said_both_ways() -> None:
    assert confirm.names("gamma", ["alpha"]) == "gamma is not among the VMs given; alpha is left out"
    assert confirm.names("alpha", ["alpha", "beta"]) == "beta is left out"
    assert confirm.names("alpha,beta,gamma,delta", ["alpha"]) == "beta, delta, gamma are not among the VMs given"
    assert confirm.names("", ["alpha"]) == "alpha is left out"
    # A typed answer is the person's: said back as one line of plain text, cut.
    said = confirm.names("x\x1b[2Jy" + "z" * 200, ["alpha"])
    assert "\x1b" not in said and "\n" not in said and len(said) < 120, said
    with pytest.raises(TypeError):
        confirm.names(None, ["alpha"])  # type: ignore[arg-type]


# ---------------------------------------------------------------- the question ----


def test_the_question_names_the_vms_their_disks_and_the_host_under_today_s_block() -> None:
    asked = destroy.question(["alpha", "beta"])
    assert "##  CONFIRM DELETION\n" in asked and "\nThis deletes alpha, beta and their disks.\n" in asked
    assert asked.endswith("Type yes to continue:")
    assert "\nThis deletes alpha on nuc and its disks.\n" in destroy.question(["alpha"], "nuc")
    assert destroy.question(["alpha"], takes="names").endswith("Type its name to continue:")
    assert destroy.question(["alpha", "beta"], takes="names").endswith("Type their names to continue:")
    # A name from Proxmox is made one line of plain text first.
    shown = destroy.question(["al\npha\x1b[31m"])
    assert "\x1b" not in shown and "This deletes al pha" in shown, shown


@pytest.mark.parametrize("answer", ["yes", "YES", "Yes", " yes ", "true", "True"])
def test_yes_takes_the_question_as_today(answer) -> None:
    assert destroy.answered(answer, ["alpha"]) is None


@pytest.mark.parametrize("answer", ["", "no", "y", "ye", "yes please", "alpha", "1", "oui"])
def test_anything_else_is_no_as_today(answer) -> None:
    assert destroy.answered(answer, ["alpha"]) == ""


def test_under_names_the_names_typed_take_it_and_any_other_is_said() -> None:
    assert destroy.answered("alpha, beta", ["beta", "alpha"], "names") is None
    assert destroy.answered("yes", ["alpha"], "names") == (
        " The names typed are not the VMs': yes is not among the VMs given; alpha is left out."
    )
    assert (
        destroy.answered("alpha", ["alpha", "beta"], "names") == " The names typed are not the VMs': beta is left out."
    )
    assert destroy.answered("", ["alpha"], "names") == ""


def test_destroy_is_built_by_this_card_and_its_handler_is_the_engine_s() -> None:
    assert VERBS["destroy"].handler == "engine.destroy:destroy"
    assert (EXITS["refused"], EXITS["failed"], EXITS["cancelled"]) == (1, 2, 130)


# ---------------------------------------------------------------- the override's rule ----


def _member(**fields: object) -> ownership.Member:
    made: dict[str, object] = {"uuid": UUID, "digest": "0" * 40, **fields}
    return ownership.Member("delta", "pve", 9013, **made)  # type: ignore[arg-type]


def test_the_override_lifts_the_refusal_of_a_member_with_no_record_and_no_other() -> None:
    bare = _member()
    assert "no record of this engine's" in ownership.refusal(bare)
    assert ownership.refusal(bare, unrecorded=True) == ""
    # Never a template, a member unread, one with no SMBIOS UUID, twins, a copy or a record of another kind.
    refused = {
        "template": _member(template=True),
        "unread": _member(unread="the configuration of delta (VMID 9013) could not be read"),
        "no uuid": _member(uuid=""),
        "twins": _member(twins=(9014,)),
        "copy": _member(recorded="vm", record_uuid="0f0e0d0c-0b0a-4908-8706-050403020100", stage="finished"),
        "other kind": _member(recorded="template", record_uuid=UUID, stage="finished"),
        "no stage": _member(recorded="vm", record_uuid=UUID),
        "first form": _member(recorded="vm", stage="cloning"),
        "no vm": _member(kind="lxc"),
    }
    for what, member in refused.items():
        assert ownership.refusal(member, unrecorded=True), what
        # Refused as ever, but where the member has no record, which is then said after the override's own.
        assert ownership.refusal(member, unrecorded=True) == ownership.refusal(member) or what in ("no uuid", "twins")
    assert "carries one UUID with VMID 9014" in ownership.refusal(refused["twins"], unrecorded=True)
    assert "template" in ownership.refusal(refused["template"], unrecorded=True)
    assert "another VM's UUID" in ownership.refusal(refused["copy"], unrecorded=True)
    assert "no SMBIOS UUID" in ownership.refusal(refused["no uuid"], unrecorded=True)
    # Only True lifts it.
    assert ownership.refusal(bare, unrecorded="yes")  # type: ignore[arg-type]


def test_a_read_that_creates_or_changes_nothing_takes_no_override() -> None:
    host = SimpleNamespace()
    with pytest.raises(ValueError):
        fleet.requested(host, ("alpha",), change=False, unrecorded=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        fleet.requested(host, ("alpha",), create=True, unrecorded=True)  # type: ignore[arg-type]


def test_only_destroy_asks_for_the_override_in_lib() -> None:
    """The override is a deletion's: no module of lib/ but destroy's, and the two that carry it to the one rule, passes
    unrecorded to a call."""
    carriers = {"lib/claude_vm/pve/ownership.py", "lib/claude_vm/engine/fleet.py"}
    found = set()
    for path in sorted((REPO / "lib" / "claude_vm").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.keyword) and node.arg == "unrecorded":
                found.add(str(path.relative_to(REPO)))
    assert found - carriers == {"lib/claude_vm/engine/destroy.py"}, found


# ---------------------------------------------------------------- against the fake ----


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@dataclasses.dataclass(frozen=True)
class Fake:
    """The fake Proxmox at base, its state in directory, and the fleet's home a whole fleet folder made against it."""

    base: str
    directory: Path
    home: Path

    def calls(self) -> list[dict]:
        """Every request the engine's client sent the fake, in order."""
        return [entry for entry in _pve_requests(self.directory) if entry["agent"] == pin.USER_AGENT]

    def deletions(self) -> list[dict]:
        return [entry for entry in self.calls() if entry["method"] == "DELETE"]

    def writes(self, since: int = 0) -> list[dict]:
        return [entry for entry in self.calls()[since:] if entry["method"] != "GET"]

    def config(self, vmid: int) -> dict:
        return _pve(self.base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]

    def listed(self) -> set[int]:
        return {int(entry["vmid"]) for entry in _pve(self.base, "GET", "cluster/resources", type="vm")["data"]}

    def destroy(self, *words: str, settings: tuple[str, ...] = OFF) -> subprocess.CompletedProcess[str]:
        """aw destroy <words>, as a script runs it: no terminal."""
        return run_aw("destroy", *words, *settings, home=self.home, timeout=120)

    def at_a_terminal(self, *words: str, typed: bytes, settings: tuple[str, ...] = OFF) -> tuple[int, str]:
        """aw destroy <words> at a terminal, typed answering its question once it is asked."""
        done: dict[str, bool] = {}

        def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
            if "typed" not in done and b"to continue:" in output:
                os.write(master, typed)
                done["typed"] = True

        rc, output = aw_at_a_terminal("destroy", *words, *settings, home=self.home, act=act, size=WINDOW, timeout=120)
        return rc, _flat(output.decode(errors="replace"))


def _uuid(fake: Fake, vmid: int) -> str:
    return proxmox.smbios_uuid(fake.config(vmid))


def _join(fake: Fake, vmid: int) -> None:
    _pve(fake.base, "PUT", "fake/pool-member", vmid=vmid, pool=ENGINE_POOL)


def _record(fake: Fake, vmid: int, **fields: object) -> None:
    """A record of this engine's written into the VM's notes: kind vm, the VM's own UUID and stage finished unless
    fields say otherwise."""
    made = {"kind": "vm", "uuid": _uuid(fake, vmid), "stage": "finished", **fields}
    notes = block.render(fake.config(vmid).get("description", ""), Record(**made).fields())
    _pve(fake.base, "PUT", f"nodes/pve/qemu/{vmid}/config", description=notes)


def _vm(fake: Fake, vmid: int, name: str, *, running: bool = False, pool: bool = True, **config: object) -> None:
    """Another VM in the fake, with no record, a member of the pool unless told."""
    _pve_vm(fake.base, vmid, name, running=running, **({"tags": "agent-warehouse"} | config))
    if pool:
        _join(fake, vmid)


def _fleet(home: Path, tmp_path: Path, pki: dict, directory_name: str = "fake"):
    directory = tmp_path / directory_name
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        _pve(base, "PUT", "fake/agent-ready-after", polls=0)
        made = whole_fleet(home, base, directory, pki)
        found = Fake(base, directory, home)
        for vmid, name in ((ALPHA, "alpha"), (BETA, "beta"), (GAMMA, "gamma")):
            _join(found, vmid)
            _record(found, vmid, name=name)
        yield found, made


@pytest.fixture
def fake(home, tmp_path, pki):
    """A whole fleet against the fake: alpha and beta running and gamma stopped, each a member of the pool with a record
    of its own."""
    for found, _ in _fleet(home, tmp_path, pki):
        yield found


def _ok(ran: subprocess.CompletedProcess[str], status: int = 0) -> None:
    assert ran.returncode == status, f"exit {ran.returncode}\n--- stdout\n{ran.stdout}\n--- stderr\n{ran.stderr}"


def _flat(text: str) -> str:
    """text with every run of white space one space, as a sentence reads however the view wrapped it."""
    return " ".join(text.split())


def _said(ran: subprocess.CompletedProcess[str]) -> str:
    return _flat(ran.stdout + ran.stderr)


def _deleted(said: str, name: str) -> bool:
    """Whether said, flat, says name's one line of a VM deleted, under its name."""
    return f"{name} deleted, with its disks." in said or f"{name}: deleted, with its disks." in said


def test_destroy_with_confirm_deletes_the_vm_and_its_disks_after_its_stop(fake) -> None:
    known = fake.home / ".ssh" / "agent-warehouse.known_hosts"
    known.parent.mkdir(exist_ok=True)
    line = f"agent-warehouse-{_uuid(fake, ALPHA)} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHRlc3Q\n"
    known.write_text(line)
    ran = fake.destroy("alpha", "--confirm=alpha")
    _ok(ran)
    assert _deleted(_said(ran), "alpha"), _said(ran)
    assert ALPHA not in fake.listed() and {BETA, GAMMA} <= fake.listed()
    # Its disks with it: destroy-unreferenced-disks alone, never purge (test_proxmox_vm_absent.py, B22).
    [deleted] = fake.deletions()
    assert deleted["path"].endswith(f"/nodes/pve/qemu/{ALPHA}") and deleted["params"] == {
        "destroy-unreferenced-disks": "1"
    }
    # It ran, so it was stopped first, as pulling its power would, and only then deleted.
    paths_sent = [(entry["method"], entry["path"]) for entry in fake.writes()]
    stop = ("POST", f"/api2/json/nodes/pve/qemu/{ALPHA}/status/stop")
    assert paths_sent.index(stop) < paths_sent.index(("DELETE", f"/api2/json/nodes/pve/qemu/{ALPHA}")), paths_sent
    # The known-hosts line stays, the tombstone of its identity; nothing else was written to the fleet's VMs.
    assert known.read_text() == line
    assert all(f"/qemu/{ALPHA}" in path for _, path in paths_sent), paths_sent


def test_a_stopped_vm_is_deleted_with_no_stop(fake) -> None:
    _ok(fake.destroy("gamma", "--confirm=gamma"))
    assert [entry["method"] for entry in fake.writes()] == ["DELETE"]
    assert GAMMA not in fake.listed()


@pytest.mark.parametrize(
    "settings",
    [
        (),
        ("--set", "destroy_save_sessions=false"),
        ("--set", "destroy_release=false"),
    ],
    ids=["both", "release", "save"],
)
def test_while_either_setting_is_on_the_run_is_refused_naming_both_cards_and_nothing_is_sent(fake, settings) -> None:
    sent = len(fake.calls())
    ran = fake.destroy("alpha", "--confirm=alpha", settings=settings)
    _ok(ran, EXITS["refused"])
    for word in (
        "card G13",
        "card G14",
        "nothing was deleted",
        "set destroy_save_sessions and destroy_release to false",
    ):
        assert word in _flat(ran.stderr), ran.stderr
    on = [name for name in ("destroy_save_sessions", "destroy_release") if f"{name}=false" not in " ".join(settings)]
    assert f"and {' and '.join(on)} {'is' if len(on) == 1 else 'are'} on" in _flat(ran.stderr), ran.stderr
    assert len(fake.calls()) == sent and ALPHA in fake.listed()


def test_with_no_terminal_and_no_confirm_the_answer_is_no_said_as_the_person_s(fake) -> None:
    sent = len(fake.calls())
    jobs = paths.jobs_dir(paths.home(REPO))
    before = sorted(jobs.iterdir()) if jobs.exists() else []
    ran = fake.destroy("alpha", "beta")
    _ok(ran, EXITS["refused"])
    assert "aw: not asked, with no terminal to ask at: Type yes to continue - no" in ran.stderr, ran.stderr
    assert "Nothing was deleted: the deletion was not confirmed." in ran.stderr
    assert "--confirm alpha,beta" in ran.stderr or "--confirm=alpha,beta" in ran.stderr, ran.stderr
    # The person's answer, never a failure; and nothing reached Proxmox, nor was any job made.
    assert "error" not in ran.stderr.lower() and "fail" not in ran.stderr.lower(), ran.stderr
    assert len(fake.calls()) == sent and {ALPHA, BETA} <= fake.listed()
    assert (sorted(jobs.iterdir()) if jobs.exists() else []) == before


def test_confirm_that_names_another_vm_is_refused_naming_it(fake) -> None:
    sent = len(fake.calls())
    ran = fake.destroy("alpha", "--confirm=beta")
    _ok(ran, EXITS["refused"])
    said = _flat(ran.stderr)
    assert "--confirm names beta, but the VMs given are alpha" in said and "nothing was deleted" in said, said
    assert len(fake.calls()) == sent


def test_confirm_is_held_to_the_names_in_the_command_s_own_process_too(fake, capsys) -> None:
    # The parse refused it already (destroy.2); the handler holds it again by the one validator, saying which differs.
    run = SimpleNamespace(set=list(OFF[1::2]), verbosity=0)
    assert destroy.destroy(run, names=["alpha"], confirm="alpha,gamma") == EXITS["refused"]
    said = capsys.readouterr().err
    assert "gamma is not among the VMs given" in said and "nothing was deleted" in said, said
    assert destroy.destroy(run, confirm="alpha") == EXITS["refused"]
    assert "no VM is given beside it" in capsys.readouterr().err
    assert fake.deletions() == []


def test_an_unrecorded_member_is_refused_and_deleted_only_under_the_override(fake) -> None:
    _vm(fake, 9013, "delta")
    ran = fake.destroy("delta", "--confirm=delta")
    _ok(ran, EXITS["refused"])
    assert "no record of this engine's" in _flat(ran.stderr) and fake.deletions() == []
    ran = fake.destroy("delta", "--confirm=delta", settings=OFF + UNRECORDED)
    _ok(ran)
    said = _said(ran)
    assert (
        "delta (VMID 9013) has no record of this fleet's: it is deleted only because destroy_allow_unrecorded" in said
    )
    assert _deleted(said, "delta"), said
    assert 9013 not in fake.listed()


@pytest.mark.parametrize("override", [False, True], ids=["off", "on"])
@pytest.mark.parametrize("what", ["template", "outside the pool", "another uuid"])
def test_destroy_never_deletes_a_template_a_vm_outside_the_pool_or_another_s_record_whatever_is_given(
    fake, what, override
) -> None:
    if what == "template":
        _vm(fake, 9013, "tmpl")
        _pve(fake.base, "POST", "nodes/pve/qemu/9013/template")
        name, says = "tmpl", "is a template, which is never a target"
    elif what == "outside the pool":
        _vm(fake, 9013, "delta", pool=False)
        name, says = "delta", f"delta is no VM of the pool {ENGINE_POOL}"
    else:
        _record(fake, BETA, name="beta", uuid=_uuid(fake, ALPHA))
        name, says = "beta", "was written for another VM, whose UUID it names"
    settings = OFF + UNRECORDED if override else OFF
    ran = fake.destroy(name, f"--confirm={name}", settings=settings)
    _ok(ran, EXITS["refused"])
    assert says in _flat(ran.stderr), ran.stderr
    assert fake.deletions() == [] and fake.writes() == []


def test_a_name_two_vms_carry_is_refused_before_anything_is_started(fake) -> None:
    _vm(fake, 9013, "beta")
    _record(fake, 9013, name="beta")
    ran = fake.destroy("beta", "--confirm=beta")
    _ok(ran, EXITS["refused"])
    said = _flat(ran.stderr)
    assert f"More than one VM of the pool {ENGINE_POOL}" in said and "(VMID 9011, 9013)" in said, said
    assert fake.writes() == []


def test_while_a_member_of_the_pool_cannot_be_read_nothing_is_deleted(fake) -> None:
    # One the token may not read may carry alpha's UUID: nothing tells the two apart (review 38's L2).
    _pve(fake.base, "PUT", "access/acl", path=f"/vms/{BETA}", roles="NoAccess", tokens=TOKEN)
    ran = fake.destroy("alpha", "--confirm=alpha")
    _ok(ran, EXITS["refused"])
    said = _flat(ran.stderr)
    assert "the token may not read the configuration of beta (VMID 9011)" in said, said
    assert "nothing is deleted" in said and fake.writes() == []


def test_one_vm_of_two_failing_is_said_not_deleted_and_the_other_is_deleted(fake) -> None:
    _pve(fake.base, "PUT", f"nodes/pve/qemu/{GAMMA}/config", protection=1)
    ran = fake.destroy("alpha", "gamma", "--confirm=alpha,gamma")
    _ok(ran, EXITS["failed"])
    said = _said(ran)
    assert _deleted(said, "alpha"), said
    assert "gamma: not deleted - its deletion failed (" in said and "protection mode enabled" in said, said
    assert "so it is still there: run" in said and "destroy gamma" in said
    assert ALPHA not in fake.listed() and GAMMA in fake.listed()


def test_a_stop_proxmox_s_file_lock_refused_is_sent_once_more(fake) -> None:
    _pve(fake.base, "PUT", "fake/task-fail", type="qmstop", error=FILE_LOCK.format(vmid=ALPHA))
    _ok(fake.destroy("alpha", "--confirm=alpha"))
    stops = [entry for entry in fake.writes() if entry["path"].endswith(f"/qemu/{ALPHA}/status/stop")]
    assert len(stops) == 2 and ALPHA not in fake.listed()


def test_a_stop_that_fails_otherwise_is_not_sent_again_and_nothing_is_deleted(fake) -> None:
    _pve(fake.base, "PUT", "fake/task-fail", type="qmstop", error="stop failed: the node is busy")
    ran = fake.destroy("alpha", "--confirm=alpha")
    _ok(ran, EXITS["failed"])
    assert "alpha: not deleted - its deletion failed (" in _said(ran) and "the node is busy" in _said(ran), _said(ran)
    assert len([entry for entry in fake.writes() if entry["path"].endswith("/status/stop")]) == 1
    assert fake.deletions() == [] and ALPHA in fake.listed()


def test_at_a_terminal_the_question_names_the_vms_and_anything_but_yes_deletes_nothing(fake) -> None:
    rc, said = fake.at_a_terminal("alpha", "beta", typed=b"no\n")
    assert rc == EXITS["refused"], said
    assert "## CONFIRM DELETION" in said and "This deletes alpha, beta and their disks." in said, said
    assert "Type yes to continue:" in said
    assert "Nothing was deleted: the deletion was not confirmed." in said
    assert fake.writes() == [] and {ALPHA, BETA} <= fake.listed()


def test_at_a_terminal_yes_deletes_them(fake) -> None:
    rc, said = fake.at_a_terminal("alpha", "gamma", typed=b"yes\n")
    assert rc == EXITS["done"], said
    assert _deleted(said, "alpha") and _deleted(said, "gamma"), said
    assert not {ALPHA, GAMMA} & fake.listed() and BETA in fake.listed()


def test_under_names_the_names_are_typed_in_place_of_yes(fake) -> None:
    names = OFF + ("--set", "destroy_confirm=names")
    rc, said = fake.at_a_terminal("gamma", typed=b"yes\n", settings=names)
    assert rc == EXITS["refused"] and "Type its name to continue:" in said, said
    assert "The names typed are not the VMs': yes is not among the VMs given; gamma is left out." in said, said
    assert GAMMA in fake.listed()
    rc, said = fake.at_a_terminal("gamma", typed=b"gamma\n", settings=names)
    assert rc == EXITS["done"], said
    assert GAMMA not in fake.listed()


def test_ctrl_c_during_the_deletion_lets_it_finish_and_says_so(fake) -> None:
    _pve(fake.base, "PUT", "fake/task-polls", polls=10**6)
    seen: dict[str, object] = {}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if "stopped" not in seen:
            if fake.deletions():
                os.kill(process.pid, signal.SIGINT)
                seen["stopped"] = time.monotonic()
        elif "alive" not in seen and time.monotonic() - float(seen["stopped"]) > 1.5:
            # Held by the deletion's shield while its task runs; then the task ends.
            seen["alive"] = process.poll() is None
            _pve(fake.base, "PUT", "fake/task-polls", polls=0)
            _pve(fake.base, "PUT", "fake/task-end")

    rc, output = aw_at_a_terminal(
        "destroy", "gamma", "--confirm=gamma", *OFF, home=fake.home, act=act, size=WINDOW, timeout=120
    )
    said = _flat(output.decode(errors="replace"))
    assert seen.get("alive") is True, said
    assert rc == EXITS["cancelled"] and "aw: stopping once the deletion of gamma is finished" in said, said
    assert _deleted(said, "gamma"), said
    assert GAMMA not in fake.listed() and len(fake.deletions()) == 1


# ---------------------------------------------------------------- one VM's deletion, in this process ----


@contextlib.asynccontextmanager
async def _open(*_: object, **__: object) -> AsyncIterator[None]:
    """A shield that holds nothing: one VM's deletion run in this process, with no job of its own."""
    yield


def _context(events: list) -> SimpleNamespace:
    return SimpleNamespace(
        job=SimpleNamespace(id="0123456789abcdef"),
        cancelled=threading.Event(),
        stopping=threading.Event(),
        emit=lambda *args, **data: events.append((args, data)),
        shield=_open,
    )


def _host() -> fleet.Host:
    where = paths.home(REPO)
    return fleet.connect(where, hosts.reach(hosts.Layout(where), Request()))


def test_a_vm_is_owned_again_after_its_stop_which_changed_its_configuration(fake, monkeypatch) -> None:
    # A stop applies a VM's pending changes, so its digest is not the one it was owned on: owned again through the
    # fleet's read, it is deleted (review 51b), where the first Owned would be refused as changed.
    import asyncio

    sent = Proxmox.stop

    def stopping(self: Proxmox, vm: fleet.Owned) -> object:
        task = sent(self, vm)
        _pve(fake.base, "PUT", f"nodes/pve/qemu/{vm.vmid}/config", cores=3)
        return task

    monkeypatch.setattr(Proxmox, "stop", stopping)
    events: list = []
    with _host() as host:
        how = destroy.How(confirmed="alpha")
        [target] = destroy._read(host, ("alpha",), how).targets
        assert asyncio.run(destroy._one(_context(events), host, target, how)) == "deleted", events
    assert ALPHA not in fake.listed()


def test_a_vm_owned_again_is_held_to_the_vm_confirmed(fake) -> None:
    # Between the question and the second look, alpha was renamed and another took its name: the VM confirmed is no
    # longer the one of the name, and nothing is deleted.
    with _host() as host:
        how = destroy.How(confirmed="alpha")
        [target] = destroy._read(host, ("alpha",), how).targets
        _pve(fake.base, "PUT", f"nodes/pve/qemu/{ALPHA}/config", name="alpha-old")
        _pve(fake.base, "PUT", f"nodes/pve/qemu/{BETA}/config", name="alpha")
        with pytest.raises(destroy.Failed) as failed:
            destroy._again(host, target, how)
    assert "is no longer alpha (VMID 9010), confirmed" in failed.value.sentence
    assert fake.deletions() == []


def test_gone_is_read_from_the_listing_at_its_vmid(fake) -> None:
    with _host() as host:
        [target] = destroy._read(host, ("gamma",), destroy.How()).targets
        assert destroy.gone(host, target.owned) is False
        _ok(fake.destroy("gamma", "--confirm=gamma"))
        assert destroy.gone(host, target.owned) is True
