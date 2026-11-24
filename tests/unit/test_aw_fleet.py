"""The fleet as the engine reads it (lib/claude_vm/engine/fleet.py; docs/architecture/engine.md §16.4).

On a whole fleet folder (tests/unit/fleet_fixture.py, made by `aw init` with nobody there) against the fake Proxmox
(tests/molecule/fake_pve_api.py), the engine's token held to its table of privileges: the pool's members are the fleet's
VMs, each with its identity, record, state from its own endpoint and address from its guest agent on its own network
card. Carried from tests/unit/test_vm_names.py, which stays as it is: a VM named after a host of the fleet is left out
of a fleet-wide read and said, and refused when named before Proxmox is asked anything; so are a name ssh reads as an
address, one this computer reaches a host by, and, for a deploy that would create, one another host's alias records;
and a name with a dot given passes. And from tests/unit/test_filters.py's guest_addresses: a sibling's address, and an
address with a newline. Then what the pool makes of today's tag: an unrecorded member, whatever tags it carries, a
template, a copy, two VMs with one UUID and a member the token may not read are never changed; a name two members carry
is refused; the listing's lag is never read; a VM of the name outside the pool is refused where /vms is readable and
said where it is not; another host's VM of the name is refused with today's sentence and a host that does not answer is
said and passed; a token refused the guest agent ends the read at once; the pool's mark naming a control VM refuses a
changing run. The rules with a test of their own: every target requested returns for a change passed owned(), the one
check, which raises for one it refuses; nothing here reads a label for a decision, or the listing's status; and the
reads send nothing but GETs.

Review 38's fixes (card G28): a changing read gives each target its Owned, which pve/ownership.py alone makes, and a
read that changes nothing gives none; every writer of the client's and engine/record.py's refuses a target and sends
nothing; a deletion of an owned VM that changed between the read and the DELETE is refused, and the VM stays; a VM
asked alone, and one a run just started, that reports the address of another running member the fleet owns is left
out; a file of certificates tls_ca_file names that cannot be used is said on each road, never a traceback; repr(Host)
holds nothing of the environment, nor the run's values; the opened host keeps what card G7's route is settled from,
its values read-only; a creating read that changes nothing is refused, and so is a name Proxmox takes for
no VM, before any host is asked; a pool's listing lags as the cluster's does, and neither is read for a VM's state; an
entry the listing gives with no name is read by its configuration; and the first form of a clone is owned only by the
run whose job it names.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import os
import socket
from pathlib import Path

import pytest
from fleet_fixture import mark_pool, whole_fleet, with_port
from harness import (
    ENGINE_POOL,
    ENGINE_ROLE,
    ENGINE_TOKEN,
    ENGINE_USER,
    REPO,
    _pve,
    _pve_requests,
    _pve_vm,
    engine_grant,
    fake_fleet,
)
from pki import proxmox_pki

from claude_vm import proxmox
from claude_vm.engine import fleet
from claude_vm.engine import record as engine_record
from claude_vm.engine.record import Record
from claude_vm.fleet import hosts, opened, overrides, paths
from claude_vm.hosts.model import Request
from claude_vm.pve import ownership, pin, privileges
from claude_vm.pve import record as block
from claude_vm.pve.client import Changed

FLEET = REPO / "lib" / "claude_vm" / "engine" / "fleet.py"
OWNERSHIP = REPO / "lib" / "claude_vm" / "pve" / "ownership.py"
TOKEN = f"{ENGINE_USER}!{ENGINE_TOKEN}"
ALPHA, BETA, GAMMA = 9010, 9011, 9012
# A run's job, as a record names one.
JOB = "2b3c4d5e6f708192"


@dataclasses.dataclass(frozen=True)
class Fake:
    """The fake Proxmox at base, its state in directory, and the fleet folder whole_fleet made against it."""

    base: str
    directory: Path
    made: object

    def calls(self) -> list[dict]:
        return _pve_requests(self.directory)

    def sent(self) -> int:
        return len(self.calls())


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def fake(home, tmp_path, pki):
    """A whole fleet against the fake: alpha and beta running and gamma stopped, each a member of the pool with a
    record of its own, its tags today's label and nothing more."""
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        _pve(base, "PUT", "fake/agent-ready-after", polls=0)
        made = whole_fleet(home, base, directory, pki)
        found = Fake(base, directory, made)
        for vmid, name in ((ALPHA, "alpha"), (BETA, "beta"), (GAMMA, "gamma")):
            _join(found, vmid)
            _record(found, vmid, name=name)
        yield found


def _config(fake: Fake, vmid: int) -> dict:
    return _pve(fake.base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]


def _uuid(fake: Fake, vmid: int) -> str:
    return proxmox.smbios_uuid(_config(fake, vmid))


def _mac(fake: Fake, vmid: int) -> str:
    return proxmox.net_mac(_config(fake, vmid)["net0"]).lower()


def _join(fake: Fake, vmid: int) -> None:
    """The VM made a member of the pool, as root makes one on the host."""
    _pve(fake.base, "PUT", "fake/pool-member", vmid=vmid, pool=ENGINE_POOL)


def _record(fake: Fake, vmid: int, **fields) -> None:
    """A record of this engine's written into the VM's notes, as a person's own text beside it stays: kind vm, the VM's
    own UUID and stage finished unless fields say otherwise."""
    made = {"kind": "vm", "uuid": _uuid(fake, vmid), "stage": "finished", **fields}
    notes = block.render(_config(fake, vmid).get("description", ""), Record(**made).fields())
    _pve(fake.base, "PUT", f"nodes/pve/qemu/{vmid}/config", description=notes)


def _vm(fake: Fake, vmid: int, name: str, *, running: bool = True, **config) -> None:
    """Another VM in the fake, a member of the pool, with no record yet."""
    _pve_vm(fake.base, vmid, name, running=running, **({"tags": "agent-warehouse"} | config))
    _join(fake, vmid)


def _host() -> fleet.Host:
    """The run's host, as a run reaches it and opens it."""
    where = paths.home(REPO)
    return fleet.connect(where, hosts.reach(hosts.Layout(where), Request()))


def _requested(*names: str, **how) -> fleet.Requested:
    with _host() as host:
        return fleet.requested(host, names, **how)


def _refused(*names: str, **how) -> str:
    with pytest.raises(fleet.Refused) as refused:
        _requested(*names, **how)
    return refused.value.sentence


def _by_name(found: fleet.Requested) -> dict[str, fleet.Target]:
    return {target.name: target for target in found.targets}


def _answer(fake: Fake, vmid: int, card: str, address: str) -> None:
    """What the VM's agent reports, as its root makes it say: one card, of that MAC, with that address."""
    given = {"ip-address": address, "ip-address-type": "ipv4", "prefix": 24}
    result = [{"name": "eth0", "hardware-address": card, "ip-addresses": [given]}]
    _pve(fake.base, "PUT", "fake/agent-answer", vmid=vmid, result=json.dumps(result))


# ------------------------------------------------------------------------------------- the fleet read whole ----


def test_a_fleet_wide_read_takes_every_recorded_member_with_its_identity_state_and_address(fake) -> None:
    found = _requested()
    by = _by_name(found)
    assert sorted(by) == ["alpha", "beta", "gamma"] and found.said == () and found.new == ()
    alpha = by["alpha"]
    assert (alpha.kind, alpha.host, alpha.node, alpha.vmid, alpha.user) == ("vm", "pve", "pve", ALPHA, "dev")
    assert alpha.identity == f"agent-warehouse-{_uuid(fake, ALPHA)}" and alpha.record.uuid == _uuid(fake, ALPHA)
    assert alpha.record.name == "alpha" and alpha.record.stage == "finished"
    # Its address on its own card, never docker0's or the loopback's.
    assert alpha.running and alpha.address == "192.0.2.60" and by["beta"].address == "192.0.2.61"
    assert not by["gamma"].running and by["gamma"].address == ""
    assert all(fleet.refusal(target) == "" for target in found.targets)
    # Each owned, with the identity and the digest of the configuration it was owned on.
    assert alpha.owned.identity == alpha.identity
    assert alpha.owned.digest == alpha.digest == _config(fake, ALPHA)["digest"]
    owning = [(target.owned.node, target.owned.vmid) for target in found.targets]
    assert owning == [("pve", ALPHA), ("pve", BETA), ("pve", GAMMA)]
    # The same members, for reading: no address read, and none owned; nor is a read that changes nothing.
    with _host() as host:
        assert [target.vmid for target in fleet.members(host)] == [ALPHA, BETA, GAMMA]
        assert {(target.address, target.owned) for target in fleet.members(host)} == {("", None)}
    assert {target.owned for target in _requested(change=False).targets} == {None}


def test_the_reads_send_nothing_but_gets(fake) -> None:
    engine_grant(fake.base, "/vms", "VM.Audit")
    _requested()
    _requested("alpha", "newvm", create=True)
    with _host() as host:
        fleet.count_made_by_agent(host)
    sent = [call for call in fake.calls() if call["agent"] == pin.USER_AGENT]
    assert sent and {call["method"] for call in sent} == {"GET"}


# ----------------------------------------------------------------------------------- names, carried from today ----


def test_a_vm_named_after_a_host_is_left_out_of_a_fleet_wide_read_and_said(fake) -> None:
    for vmid, name in ((9013, "pve"), (9014, "localhost")):
        _vm(fake, vmid, name)
        _record(fake, vmid, name=name)
    found = _requested()
    assert sorted(_by_name(found)) == ["alpha", "beta", "gamma"]
    assert len(found.said) == 1 and found.said[0].startswith("Left out: pve, localhost, members of the pool aw-fleet")


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("pve", "pve is the name of a host of the fleet's (localhost, pve), which no VM may take"),
        ("localhost", "localhost is the name of a host of the fleet's"),
        # Read as an address by ssh, as 3232235777 is 192.168.1.1.
        ("3232235777", "ssh reads it as an address"),
        ("al,pha", "a name given is no VM's name"),
    ],
)
def test_a_name_no_vm_may_have_is_refused_before_proxmox_is_asked(fake, name, says) -> None:
    before = fake.sent()
    assert says in _refused("alpha", name, create=True)
    assert fake.sent() == before


def test_a_name_this_computer_reaches_a_host_by_is_refused_before_proxmox_is_asked(fake) -> None:
    # A host of the fleet reached by a name: an alias of that name would take this computer's connections there.
    with_port(8006, "lab", "lab.example")
    before = fake.sent()
    sentence = _refused("alpha", "lab.example", create=True)
    assert "lab.example" in sentence and "it names a host of the inventory" in sentence
    assert fake.sent() == before


@pytest.mark.parametrize("name", ["my_vm", "a" * 64, "alpha-", "alpha."])
def test_a_name_to_create_that_proxmox_takes_for_no_vm_is_refused_before_any_host_is_asked(fake, name) -> None:
    before = fake.sent()
    sentence = _refused("alpha", name, create=True)
    assert sentence == fleet.NOT_PROXMOX_S and name not in sentence
    assert fake.sent() == before


def test_a_deploy_that_would_create_is_a_changing_run(fake) -> None:
    before = fake.sent()
    with pytest.raises(ValueError, match="create is asked with change"):
        _requested("newvm", create=True, change=False)
    assert fake.sent() == before


def test_a_name_with_a_dot_given_passes_the_names_checks(fake) -> None:
    found = _requested("alpha.lab", create=True)
    assert found.new == ("alpha.lab",)


def test_with_the_aliases_off_a_name_ssh_would_take_is_no_refusal(fake) -> None:
    from claude_vm.fleet import overrides

    where = paths.home(REPO)
    overrides.set(where, "claude_ssh_config", False, None, overrides.read(where, None).client.digest)
    assert _requested("3232235777", create=True).new == ("3232235777",)


def test_a_deploy_that_creates_with_no_names_asks_for_the_default_vm_and_any_other_run_for_every_member(fake) -> None:
    assert _requested(create=True).new == ("agent-warehouse-default",)
    assert sorted(_by_name(_requested(change=False))) == ["alpha", "beta", "gamma"]


def test_a_name_given_that_no_member_carries_is_missing_but_for_a_deploy_that_creates(fake) -> None:
    with pytest.raises(fleet.Missing) as missing:
        _requested("alpha", "delta")
    assert missing.value.names == ("delta",)
    assert missing.value.sentence.startswith("delta is no VM of the pool aw-fleet on pve:")
    found = _requested("alpha", "delta", create=True)
    assert [target.name for target in found.targets] == ["alpha"] and found.new == ("delta",)


def test_a_name_two_members_carry_is_refused_named_and_left_out_fleet_wide(fake) -> None:
    for vmid in (9013, 9014):
        _vm(fake, vmid, "delta")
        _record(fake, vmid, name="delta")
    sentence = _refused("delta")
    assert sentence.startswith("More than one VM of the pool aw-fleet on pve is named delta (VMID 9013, 9014)")
    found = _requested()
    assert sorted(_by_name(found)) == ["alpha", "beta", "gamma"]
    assert found.said == (
        "Left out: delta, each the name of more than one VM of the pool aw-fleet, and a VM is found by its name. Rename"
        " one of each in Proxmox.",
    )


# ------------------------------------------------------------------------------------------ what is owned ----


def test_a_member_with_no_record_is_never_changed_whatever_tags_it_carries(fake) -> None:
    # Today's ownership tag, and the label, are only tags: no record, no target for a change.
    _vm(fake, 9013, "delta", tags="agent-warehouse;agent-warehouse-sized")
    with pytest.raises(fleet.NotOwned) as refused:
        _requested("delta")
    assert "delta (VMID 9013) is a member of the pool with no record of this engine's" in refused.value.sentence
    found = _requested()
    assert "delta" not in _by_name(found)
    assert found.said[0].startswith("Left out: delta (VMID 9013) is a member of the pool with no record")
    # A run that changes nothing reads it, with why it is not the fleet's to change.
    target = _by_name(_requested("delta", change=False))["delta"]
    assert target.record is None and "no record of this engine's" in fleet.refusal(target)
    # And a deploy that would create it is refused: it is a VM of the name, which the fleet did not make.
    with pytest.raises(fleet.NotOwned):
        _requested("delta", create=True)


def test_a_template_is_never_a_target(fake) -> None:
    _vm(fake, 9013, "tmpl", running=False)
    _record(fake, 9013, kind="template", name="tmpl")
    _pve(fake.base, "POST", "nodes/pve/qemu/9013/template")
    # No target of any read, and nothing to say of it: the fleet's templates are its own, and no VM.
    found = _requested()
    assert "tmpl" not in _by_name(found) and found.said == ()
    assert "tmpl" not in _by_name(_requested(change=False))
    with pytest.raises(fleet.NotOwned) as refused:
        _requested("tmpl")
    assert refused.value.sentence == "tmpl (VMID 9013) is a template, which is never a target"


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        # Another VM's record, as a clone sent without a description carries its source's: refused here, and by the
        # record's own reader once it refuses a copy (card E11's Copy).
        ({"uuid": "alpha"}, "another VM"),
        # A template's record on a VM that is none: a copy, whatever its UUID.
        ({"kind": "template"}, "beta (VMID 9011) holds a template's record and is no template, so it is a copy"),
        ({"kind": "golden"}, "holds a golden's record and is no template"),
        # The first form a clone carries, which names no UUID.
        ({"uuid": "", "stage": "cloning"}, "beta (VMID 9011)'s record is still the first form its clone carries"),
    ],
)
def test_a_copy_of_a_record_and_a_first_form_are_never_changed(fake, fields, says) -> None:
    if fields.get("uuid") == "alpha":
        fields = {**fields, "uuid": _uuid(fake, ALPHA), "name": "alpha"}
    _record(fake, BETA, **fields)
    with pytest.raises(fleet.NotOwned) as refused:
        _requested("beta")
    assert says in refused.value.sentence and "9011" in refused.value.sentence
    found = _requested()
    assert sorted(_by_name(found)) == ["alpha", "gamma"] and says in found.said[0]


def test_two_vms_with_one_uuid_are_both_refused(fake) -> None:
    # A restore without unique keeps its source's SMBIOS UUID: neither can be told from the other.
    _pve(fake.base, "PUT", f"nodes/pve/qemu/{BETA}/config", smbios1=_config(fake, ALPHA)["smbios1"])
    _record(fake, BETA, name="beta")
    found = _requested()
    assert sorted(_by_name(found)) == ["gamma"]
    for name, other in (("alpha", BETA), ("beta", ALPHA)):
        with pytest.raises(fleet.NotOwned) as refused:
            _requested(name)
        assert f"carries one UUID with VMID {other}" in refused.value.sentence


def test_a_member_the_token_may_not_read_is_said_and_never_changed(fake) -> None:
    # The pool's listing names every member, one the token holds NoAccess on too, whose configuration is then a 403.
    _pve(fake.base, "PUT", "access/acl", path=f"/vms/{BETA}", roles="NoAccess", tokens=TOKEN)
    found = _requested()
    assert sorted(_by_name(found)) == ["alpha", "gamma"]
    assert found.said[0].startswith("Left out: the token may not read the configuration of beta (VMID 9011)")
    with pytest.raises(fleet.NotOwned) as refused:
        _requested("beta")
    assert "never changed or deleted" in refused.value.sentence
    # Nothing takes it for gone: a run that changes nothing still has it, unread.
    target = _by_name(_requested(change=False))["beta"]
    assert target.unread and target.record is None


def test_an_unreadable_record_is_never_changed(fake) -> None:
    notes = "\n".join(["agent-warehouse record (format 9)", "kind: vm", block.END])
    _pve(fake.base, "PUT", f"nodes/pve/qemu/{BETA}/config", description=notes)
    with pytest.raises(fleet.NotOwned) as refused:
        _requested("beta")
    assert "VM 9011's" in refused.value.sentence and "never changed or deleted" in refused.value.sentence


def test_owned_is_the_one_check_and_raises_for_a_target_it_refuses(fake, monkeypatch) -> None:
    passed = []
    check = fleet.owned

    def recorded(target):
        passed.append(target.vmid)
        return check(target)

    monkeypatch.setattr(fleet, "owned", recorded)
    found = _requested()
    assert sorted(passed) == sorted(target.vmid for target in found.targets) == [ALPHA, BETA, GAMMA]
    unrecorded = dataclasses.replace(found.targets[0], record=None)
    with pytest.raises(fleet.NotOwned):
        check(unrecorded)
    # What it gives is an Owned, of the same VM as the one the read gave the target.
    made = check(found.targets[0])
    assert isinstance(made, ownership.Owned) and made == found.targets[0].owned


def test_an_owned_is_made_by_owned_alone_and_never_by_hand_or_a_copy_s_change(fake) -> None:
    alpha = _by_name(_requested("alpha"))["alpha"]
    for made in (
        lambda: ownership.Owned("alpha", "pve", ALPHA, "", ""),
        lambda: ownership.Owned(fleet._as_member(alpha)),
        lambda: ownership.Owned(fleet._as_member(alpha), object()),
        lambda: dataclasses.replace(alpha.owned, vmid=BETA),
        lambda: copy.replace(alpha.owned, vmid=BETA),
    ):
        with pytest.raises(TypeError, match="made by ownership.owned alone"):
            made()
    with pytest.raises(dataclasses.FrozenInstanceError):
        alpha.owned.vmid = BETA
    # A target changed by hand is held as its fields now say, and owns nothing it did not own.
    with pytest.raises(fleet.NotOwned):
        fleet.owned(dataclasses.replace(alpha, record=None))


def test_every_writer_refuses_a_target_and_sends_nothing(fake) -> None:
    found = _requested("alpha")
    alpha = found.targets[0]
    # The client's refusal, and the record's writer's, each its own: the writer refuses before it reaches the client.
    client, writer = "is written to only as an Owned", "record and label are written only to an Owned"
    with _host() as host:
        before = fake.sent()
        writes = (
            (lambda vm: host.client.write_config(vm, {"cores": 2}, alpha.digest), client),
            (lambda vm: host.client.edit_config(vm, lambda config: {"cores": 2}), client),
            (lambda vm: host.client.resize(vm, "scsi0", "+1G"), client),
            (lambda vm: host.client.start(vm), client),
            (lambda vm: host.client.shutdown(vm), client),
            (lambda vm: host.client.stop(vm), client),
            (lambda vm: host.client.delete(vm), client),
            (lambda vm: engine_record.write(host.client, vm, lambda current: current), writer),
            (lambda vm: engine_record.label(host.client, vm, "agent-warehouse"), writer),
        )
        for write, says in writes:
            for given in (alpha, dataclasses.replace(alpha, owned=None), fleet._as_member(alpha)):
                with pytest.raises(TypeError, match=says):
                    write(given)
        assert fake.sent() == before
        # The target's Owned, the one way to change it.
        engine_record.write(host.client, alpha.owned, lambda current: dataclasses.replace(current, job=JOB))
    assert block.parse(_config(fake, ALPHA)["description"])["job"] == JOB


def test_a_deletion_of_an_owned_vm_that_changed_since_the_read_is_refused_and_it_stays(fake) -> None:
    alpha = _by_name(_requested("alpha"))["alpha"]
    # A person's change between the read and the deletion: Proxmox's DELETE takes no digest, so it is read again.
    _pve(fake.base, "PUT", f"nodes/pve/qemu/{ALPHA}/config", cores=3)
    with _host() as host, pytest.raises(Changed) as changed:
        host.client.delete(alpha.owned)
    assert "changed since the read it was owned on" in str(changed.value)
    assert _config(fake, ALPHA)["name"] == "alpha"
    assert [call for call in fake.calls() if call["method"] == "DELETE"] == []
    # Read again, it is owned again, by the read made now.
    again = _by_name(_requested("alpha"))["alpha"]
    assert again.owned.digest != alpha.owned.digest and again.owned.digest == _config(fake, ALPHA)["digest"]


def test_the_first_form_of_a_clone_is_owned_only_by_the_run_whose_job_it_names(fake) -> None:
    first = {"uuid": "", "stage": "cloning", "name": "delta", "job": JOB}
    _vm(fake, 9013, "delta")
    _record(fake, 9013, **first)
    delta = _by_name(_requested("delta", change=False))["delta"]
    for job in ("", "0123456789abcdef"):
        with pytest.raises(fleet.NotOwned) as refused:
            fleet.owned(delta, job=job)
        assert "still the first form its clone carries" in refused.value.sentence
    made = fleet.owned(delta, job=first["job"])
    assert (made.vmid, made.identity) == (9013, delta.identity)
    # The run writes the rest through it, and the VM is then owned by any run.
    with _host() as host:
        engine_record.write(
            host.client, made, lambda current: dataclasses.replace(current, uuid=_uuid(fake, 9013), stage="unfinished")
        )
    assert _by_name(_requested("delta"))["delta"].owned is not None


# --------------------------------------------------------------------------------------------- addresses ----


def test_two_vms_that_report_one_address_are_both_left_out(fake) -> None:
    # beta's agent reports alpha's address, on beta's own card: nothing tells which is telling the truth.
    _answer(fake, BETA, _mac(fake, BETA), "192.0.2.60")
    found = _requested()
    by = _by_name(found)
    assert by["alpha"].address == by["beta"].address == ""
    assert by["alpha"].shared == by["beta"].shared == "192.0.2.60"
    said = (
        "alpha (VMID 9010), beta (VMID 9011) report one address (192.0.2.60), so none of them is reached: nothing here"
        " can tell which is telling the truth. Look at each one's address in Proxmox's console."
    )
    assert found.said == (said,)
    # Named alone, each is left out all the same, and said with its sibling: its address is held against every running
    # member's the fleet owns, a target of the read or not.
    for name in ("alpha", "beta"):
        alone = _requested(name)
        assert (alone.targets[0].address, alone.targets[0].shared, alone.said) == ("", "192.0.2.60", (said,))
    # gamma, which reports its own, keeps it.
    assert _by_name(_requested("gamma"))["gamma"].address == ""
    _pve(fake.base, "POST", f"nodes/pve/qemu/{GAMMA}/status/start")
    assert _by_name(_requested("gamma"))["gamma"].address == "192.0.2.62"


def test_a_vm_a_run_just_started_that_reports_a_sibling_s_address_is_not_given_it(fake) -> None:
    # gamma, started by the run, its agent reporting alpha's address on its own card: whatever answers there would be
    # taken for gamma, its host key filed under gamma's identity at its first use.
    gamma = _by_name(_requested("gamma"))["gamma"]
    _pve(fake.base, "POST", f"nodes/pve/qemu/{GAMMA}/status/start")
    _answer(fake, GAMMA, _mac(fake, GAMMA), "192.0.2.60")
    with _host() as host:
        with pytest.raises(fleet.Shared) as shared:
            fleet.address(host, gamma)
        assert shared.value.address == "192.0.2.60"
        assert shared.value.sentence.startswith("gamma (VMID 9012), alpha (VMID 9010) report one address (192.0.2.60)")
        # Its own address, once it reports it, is its.
        _answer(fake, GAMMA, _mac(fake, GAMMA), "192.0.2.70")
        assert fleet.address(host, gamma) == "192.0.2.70"
        # A stopped sibling's agent says nothing: beta stopped, gamma may report what beta did.
        _pve(fake.base, "POST", f"nodes/pve/qemu/{BETA}/status/stop")
        _answer(fake, GAMMA, _mac(fake, GAMMA), "192.0.2.61")
        assert fleet.address(host, gamma) == "192.0.2.61"


@pytest.mark.parametrize("given", ["192.0.2.7\nProxyCommand nc 203.0.113.9 22", "0.0.0.0", "224.0.0.1", "240.0.0.1"])
def test_an_address_that_is_not_one_ipv4_address_a_card_answers_at_is_none(fake, given) -> None:
    _answer(fake, ALPHA, _mac(fake, ALPHA), given)
    assert _by_name(_requested("alpha"))["alpha"].address == ""


def test_an_address_on_another_card_is_never_taken(fake) -> None:
    _answer(fake, ALPHA, "02:42:9a:11:22:33", "192.0.2.77")
    assert _by_name(_requested("alpha"))["alpha"].address == ""


def test_the_listing_s_lag_is_never_read_for_what_a_run_just_changed(fake) -> None:
    # The pool's listing lags as the cluster's does: Proxmox fills both from the status pvestatd last polled.
    _pve(fake.base, "PUT", "fake/listing-lag", listings=5)
    _pve(fake.base, "POST", f"nodes/pve/qemu/{GAMMA}/status/start")
    gamma = _by_name(_requested("gamma"))["gamma"]
    assert gamma.running and gamma.address == "192.0.2.62"
    # Both listings still say it is stopped.
    with _host() as host:
        listed = {entry.vmid: entry.status for entry in host.client.resources("vm")}
        pooled = {entry.vmid: entry.status for entry in host.client.pool(ENGINE_POOL).members}
    assert listed[GAMMA] == pooled[GAMMA] == "stopped"


def test_a_token_refused_the_guest_agent_ends_the_read_at_once(fake) -> None:
    # The token's role on the pool without the agent's privilege: Proxmox's 403, never "no address yet".
    held = privileges.needed("pool") - {fleet.AGENT_PRIVILEGE}
    _pve(fake.base, "POST", "access/roles", roleid="NoAgent", privs=",".join(sorted(held)))
    pool = f"/pool/{ENGINE_POOL}"
    _pve(fake.base, "PUT", "access/acl", path=pool, roles=ENGINE_ROLE, tokens=TOKEN, delete=1)
    _pve(fake.base, "PUT", "access/acl", path=pool, roles="NoAgent", tokens=TOKEN)
    before = fake.sent()
    sentence = _refused()
    assert "refused the token the privilege it needs (403)" in sentence and fleet.AGENT_PRIVILEGE in sentence
    # The first VM's refusal ended it: beta's agent was never asked.
    asked = [call["path"] for call in fake.calls()[before:] if call["path"].endswith("network-get-interfaces")]
    assert len(asked) == 1


# -------------------------------------------------------------------------------- a deploy that would create ----


def test_a_vm_of_the_name_outside_the_pool_is_refused_where_the_token_reads_every_vm(fake) -> None:
    _pve_vm(fake.base, 9013, "delta", running=False)
    engine_grant(fake.base, "/vms", "VM.Audit")
    sentence = _refused("delta", create=True)
    assert sentence.startswith('A VM named "delta" already exists (VMID 9013) and is no member of the pool aw-fleet')
    # The template outside the pool too, by its own sentence.
    assert _refused("ubuntu-24.04-cloudinit", create=True).startswith(
        '"ubuntu-24.04-cloudinit" is the name of a template (VMID 9000) outside the pool aw-fleet'
    )
    # A run that creates nothing never looks outside the pool.
    with pytest.raises(fleet.Missing):
        _requested("delta")


def test_a_vm_of_the_name_outside_the_pool_is_not_looked_for_where_the_token_reads_the_pool_alone(fake) -> None:
    _pve_vm(fake.base, 9013, "delta", running=False)
    found = _requested("delta", create=True)
    assert found.new == ("delta",) and found.outside is False
    assert found.said == (
        "The token may not read every VM of pve (VM.Audit on /vms, proxmox_audit_scope: pool), so no VM named delta"
        " outside the pool aw-fleet was looked for: one there would carry the name a second time.",
    )


def test_a_clone_in_flight_outside_the_pool_is_said_as_one(fake) -> None:
    engine_grant(fake.base, "/vms", "VM.Audit")
    _pve(fake.base, "PUT", "fake/task-polls", polls=1000)
    _pve(fake.base, "POST", "nodes/pve/qemu/9000/clone", newid=9020, name="delta", pool=ENGINE_POOL)
    try:
        sentence = _refused("delta", create=True)
    finally:
        _pve(fake.base, "PUT", "fake/task-end")
    assert sentence.startswith('A VM named "delta" (VMID 9020) is being cloned, its configuration under lock: clone')
    assert "already exists" not in sentence


def test_an_entry_listed_with_no_name_is_read_by_its_configuration_before_a_creating_read_passes_it(fake) -> None:
    # The cluster lists a guest its node has not reported yet with no name: a VM made seconds ago, a clone in flight.
    engine_grant(fake.base, "/vms", "VM.Audit")
    _pve_vm(fake.base, 9013, "delta", running=False)
    _pve(fake.base, "PUT", "fake/unnamed", vmid=9013)
    with _host() as host:
        assert {entry.vmid: entry.name for entry in host.client.resources("vm")}[9013] == ""
    assert _refused("delta", create=True).startswith('A VM named "delta" already exists (VMID 9013)')
    # One of another name is passed; one whose node is down, said, as one not looked at.
    found = _requested("echo", create=True)
    assert found.new == ("echo",) and found.outside is True
    unread = [line for line in found.said if "listed with no name" in line]
    assert [line.split(",")[0] for line in unread] == [f"VMID {v} on pve2 is listed with no name" for v in (8000, 8001)]
    assert all("595" in line and line.endswith("whether it is a VM named echo is not known.") for line in unread)


def test_a_clone_in_flight_listed_with_no_name_is_refused_though_its_name_cannot_be_read(fake) -> None:
    engine_grant(fake.base, "/vms", "VM.Audit")
    _pve(fake.base, "PUT", "fake/task-polls", polls=1000)
    _pve(fake.base, "POST", "nodes/pve/qemu/9000/clone", newid=9020, name="delta", pool=ENGINE_POOL)
    _pve(fake.base, "PUT", "fake/unnamed", vmid=9020)
    try:
        # Its configuration names it, as the fake's clone does: said as the clone in flight of that name.
        assert _refused("delta", create=True).startswith('A VM named "delta" (VMID 9020) is being cloned')
        # Proxmox's own holds its lock and no name yet, until the worker writes the rest: it may be any name's.
        _pve(fake.base, "PUT", "fake/unnamed", vmid=9020, configuration=1)
        sentence = _refused("echo", create=True)
    finally:
        _pve(fake.base, "PUT", "fake/task-end")
    assert sentence.startswith("VMID 9020 is being cloned, its configuration under lock: clone and naming no VM yet")
    assert "it may be one named echo" in sentence


def test_the_count_of_vms_an_agent_made_counts_the_fleet_s_own(fake) -> None:
    _record(fake, ALPHA, name="alpha", made_by_agent=True)
    _record(fake, BETA, name="beta", made_by_agent=True)
    # A copy of beta's record in another member's notes is not the fleet's, and counts nothing.
    _vm(fake, 9013, "copy")
    _record(fake, 9013, name="beta", uuid=_uuid(fake, BETA), made_by_agent=True)
    with _host() as host:
        assert fleet.count_made_by_agent(host) == 2


# ------------------------------------------------------------------------------ another host of the fleet ----


@pytest.fixture
def nuc(home, tmp_path, fake, pki):
    """A second host of the fleet, nuc, its own fake Proxmox with delta a member of its pool: its address."""
    directory = tmp_path / "nuc"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        whole_fleet(home, base, directory, pki, host="nuc", fleet=fake.made.fleet)
        _pve_vm(base, 9020, "delta")
        _pve(base, "PUT", "fake/pool-member", vmid=9020, pool=ENGINE_POOL)
        yield base


def _free_port() -> int:
    """A port of the loopback's that nothing listens at."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _aliases(*blocks: tuple[str, str]) -> None:
    """The managed SSH config under this test's home, each block an alias recording its host."""
    folder = paths.user_home(os.environ) / ".ssh"
    folder.mkdir(exist_ok=True)
    text = "".join(
        f"# BEGIN agent-warehouse: {name}\n# origin: {origin}\nHost {name}\n    HostName 192.0.2.99\n"
        f"# END agent-warehouse: {name}\n"
        for name, origin in blocks
    )
    (folder / "agent-warehouse.conf").write_text(text)


def test_a_name_another_host_s_alias_records_is_refused_before_proxmox_is_asked(fake, nuc) -> None:
    _aliases(("delta", "nuc"))
    before = fake.sent()
    sentence = _refused("alpha", "delta", create=True)
    assert sentence.startswith("delta is a VM of nuc, as the SSH alias of the name records in")
    assert fake.sent() == before


def test_a_name_another_host_has_is_refused_with_today_s_sentence(fake, nuc) -> None:
    assert _refused("delta", create=True) == (
        "delta is a VM of nuc already, and a VM name belongs to one host: aw deploy delta --host nuc runs it there, or"
        " give this one another name"
    )
    # A name nuc does not have is one to make here.
    assert _requested("echo", create=True).new == ("echo",)


def _anchorless(host: str) -> None:
    """host keeps no anchor, so that its client verifies against the computer's certificates, with the file tls_ca_file
    names added: the setting names a file that is not there."""
    where = paths.home(REPO)
    layout = hosts.Layout(where)
    hosts.write(layout, host, layout.state(host).digest, anchor=None)
    assert layout.anchor(host, layout.fields(host)) is None
    missing = str(where.root / "no-such-ca.pem")
    overrides.set(where, "tls_ca_file", missing, None, overrides.read(where, None).client.digest)


def test_a_file_of_certificates_that_cannot_be_used_is_said_on_each_road_never_a_traceback(fake, nuc) -> None:
    # Another host's, while it asks whether that host has a VM of the name: said, and passed.
    _anchorless("nuc")
    found = _requested("delta", create=True)
    assert found.new == ("delta",) and found.said[-1].startswith("nuc did not answer")
    # The run's host's, as it opens it: one sentence, which names the setting.
    where = paths.home(REPO)
    reached = hosts.reach(hosts.Layout(where), Request())
    anchorless = dataclasses.replace(
        reached, anchor=None, fields=dataclasses.replace(reached.fields, trust_kind=None, fingerprint="")
    )
    with pytest.raises(fleet.Refused) as refused:
        fleet.connect(where, anchorless)
    assert refused.value.sentence.startswith("pve's API cannot be reached as its files say:")
    assert "tls_ca_file" in refused.value.sentence


def test_an_opened_host_s_repr_holds_nothing_of_the_environment(fake, monkeypatch) -> None:
    # A proxy that needs a password is given in HTTPS_PROXY, as every secret a person exported is in the environment.
    mark = "pw-g28-repr-mark"
    monkeypatch.setenv("HTTPS_PROXY", f"http://someone:{mark}@proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("G28_EXPORTED", f"{mark}-exported")
    where = paths.home(REPO)
    with fleet.connect(where, hosts.reach(hosts.Layout(where), Request())) as host:
        shown = repr(host)
    assert mark not in shown and "HTTPS_PROXY" not in shown and "G28_EXPORTED" not in shown
    # Nor the run's values (card G7's), a path among them made from HOME and vm_ssh_options what a person wrote.
    assert "vm_ssh_route" not in shown and "values=" not in shown
    assert shown.startswith("Host(name='pve'")


def test_an_opened_host_keeps_what_its_route_is_settled_from(fake) -> None:
    # Card G7's fields, which engine/routes.py settles a route from, carried with the opening of a host into
    # fleet/opened.py (card G28): the fleet's home, host.yml as it was read, and the run's values, read-only.
    where = paths.home(REPO)
    reached = hosts.reach(hosts.Layout(where), Request())
    with opened.connect(where, reached) as host:
        assert host.home == where and host.fields == reached.fields
        assert host.values["vm_ssh_route"] == overrides.resolve(where, reached.host.name)["vm_ssh_route"]
        assert host.values["vm_ssh_route"].value == "direct"
        with pytest.raises(TypeError):
            host.values["vm_ssh_route"] = overrides.Value("vm_ssh_route", "host", overrides.RUN)


def test_the_engine_opens_a_host_and_judges_a_member_by_what_status_may_import() -> None:
    # One way to open a host and one ownership rule, which status reads by too (card G16): the engine's are those.
    assert (fleet.connect, fleet.Host, fleet.Refused) == (opened.connect, opened.Host, opened.Refused)
    assert issubclass(fleet.NotOwned, opened.Refused) and fleet.Owned is ownership.Owned
    assert fleet.IDENTITY == ownership.IDENTITY and fleet.KIND == ownership.KIND


def test_a_host_that_does_not_answer_is_said_and_passed(fake, nuc) -> None:
    with_port(_free_port(), "nuc")
    found = _requested("delta", create=True)
    assert found.new == ("delta",)
    assert found.said[-1] == (
        "nuc did not answer, so whether it has a VM of these names is not known: its alias, once written, records it"
    )


# --------------------------------------------------------------------------------------- the pool's mark ----


def test_a_pool_whose_mark_names_a_control_vm_refuses_a_changing_run_before_anything_else(fake) -> None:
    controller = "11111111-1111-4111-8111-111111111111"
    mark_pool(fake.base, fake.made.fleet, controller=controller)
    before = fake.sent()
    assert _refused() == f"this fleet has a control VM ({controller}): change it there, or retire it"
    asked = [call["path"] for call in fake.calls()[before:]]
    assert all("/qemu/" not in path for path in asked), asked
    # A run that changes nothing reads it.
    assert sorted(_by_name(_requested(change=False))) == ["alpha", "beta", "gamma"]


def test_a_pool_with_no_mark_or_another_fleet_s_refuses_a_changing_run(fake) -> None:
    _pve(fake.base, "PUT", "pools", poolid=ENGINE_POOL, comment="a note of the person's own")
    assert "carries no mark of a fleet" in _refused("alpha")
    mark_pool(fake.base, "22222222-2222-4222-8222-222222222222")
    assert "the pool's mark names another fleet" in _refused("alpha")
    # A run that changes nothing reads it.
    assert [target.name for target in _requested("alpha", change=False).targets] == ["alpha"]


# -------------------------------------------------------------------------------------- the rules by scans ----


def _functions(path: Path = FLEET) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text())
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _reading(attribute: str, path: Path = FLEET) -> set[str]:
    """The functions of the module at path, engine/fleet.py unless told, that read attribute of anything."""
    return {
        name
        for name, function in _functions(path).items()
        if any(isinstance(node, ast.Attribute) and node.attr == attribute for node in ast.walk(function))
    }


def test_requested_returns_a_target_for_a_change_through_owned_and_the_one_rule_alone_decides() -> None:
    called = {
        node.func.id
        for node in ast.walk(_functions()["requested"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "owned" in called
    # The one check is pve/ownership.py's refusal, which owned raises: nothing here holds a record to the VM it is in,
    # and there no function but refusal reads the UUID a record names, nor makes an Owned but owned.
    assert _reading("uuid") == set() and _reading("record_uuid", OWNERSHIP) == {"refusal"}
    making = {
        name
        for name, function in _functions(OWNERSHIP).items()
        if any(isinstance(node, ast.Name) and node.id == "_MINT" for node in ast.walk(function))
    }
    assert making == {"__init__", "owned"}


def test_nothing_here_reads_a_label_or_the_listing_s_status_for_a_decision() -> None:
    tree = ast.parse(FLEET.read_text())
    labels = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "tags"]
    named = [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value in ("vm_label", "tags")
    ]
    assert labels == [] and named == []
    # A VM's state is read from its own endpoint, in _running alone.
    assert _reading("status") == {"_running"}
