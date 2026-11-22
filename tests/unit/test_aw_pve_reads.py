"""The Proxmox client's reads (lib/claude_vm/pve/client.py, answers.py and pin.py; docs/architecture/engine.md §16.5).

Every read goes to the fake Proxmox (tests/molecule/fake_pve_api.py) through harness.fake_fleet, serving a node's
certificate that tests/unit/pki.py made, with the new engine's pool and its one token, granted as init grants it and
held to the engine's table of privileges (enforce): every request a read sends is a call the table has a row for,
answered only where the token holds that row's privilege. The client sends nothing until the certificate verifies
against what the host keeps - its anchor, the node's own certificate (with its fingerprint recorded) or its CA, or with
no anchor the computer's certificates - and a certificate that is not what the host keeps leaves the fake's request log
without one request of the client's, with the remedy told by OpenSSL's verify code: a certificate that does not name the
address it was reached at (one that names 127.0.0.2, the fake at 127.0.0.1) an address to change, or the node's own
certificate to keep, which reaches the host there, held to its fingerprint and not to a name; any other, aw init, with
tls_ca_file second where no anchor is kept and the issuer is unknown; one out of its dates, what to renew, or the clock.
No context logs its session's keys under SSLKEYLOGFILE, or opens the file it names. The first request is GET /version,
two first calls at once each within their own deadline, and a host below 9 is refused before any other. A redirect is an
answer; a 401 or a 403 is Denied by its status alone; a node that is down fails its own reads alone; an answer nested
deeper than any of Proxmox's is Malformed. The rules at the end hold the token to one class: PVEAPIToken is written in
one module of the new engine, no method returns the token, and no error says it.
"""

from __future__ import annotations

import ast
import calendar
import dataclasses
import logging
import re
import ssl
import threading
import time
import types
from pathlib import Path

import pytest
from harness import (
    ENGINE_POOL,
    ENGINE_TOKEN,
    ENGINE_USER,
    REPO,
    _pve,
    _pve_requests,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from test_aw_transport import _new_engine, _rel, _said, _Tunnel

from claude_vm import proxmox, trust
from claude_vm.pve import answers, pin, privileges
from claude_vm.pve.client import (
    EXPIRED,
    MISNAMED,
    NOT_YET_VALID,
    Denied,
    Malformed,
    Proxmox,
    Refused,
    Token,
    TooOld,
    Unverified,
)
from claude_vm.secret import Secret
from claude_vm.vendors import proxy
from claude_vm.vendors.transport import UNKNOWN_ISSUER, Answer, NotMade, TimedOut

# The module's three certificate authorities: the fake's, one that signed nothing the fake shows, and one whose node's
# certificate names another address than the fake's. Cheap enough to make again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "other_pki", "misnamed_pki"}
CLIENT = REPO / "lib" / "claude_vm" / "pve" / "client.py"
# A token's secret no output may show: low in entropy, so that gitleaks takes it for no key.
CANARY = "unit-test-pve-client-canary-value"
GIB = 1024**3


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(scope="module")
def other_pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("other-pki"), key_usage=True, name="Another Test CA")


@pytest.fixture(scope="module")
def misnamed_pki(tmp_path_factory):
    """A node's certificate that names 127.0.0.2, where the fake is reached at 127.0.0.1: as a host's own certificate
    names its node and its LAN address, and never the tailnet's address, or a forwarded port's, it is reached at."""
    return proxmox_pki(tmp_path_factory.mktemp("misnamed-pki"), key_usage=True, address="127.0.0.2")


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

    def paths(self) -> list[str]:
        return [entry["path"].removeprefix("/api2/json/") for entry in self.asked()]

    def client(self, pinned: pin.Pin | None = None, token: Token | None = None, **kwargs) -> Proxmox:
        return Proxmox(self.base, pinned or pin.kept(self.pki["ca"]), token or self.token, **kwargs)


def _fleet(directory: Path, served: dict, **kwargs):
    """The fake with the engine's token held to its table, granted, beside the role on its pool, what init grants by
    default (card E1): VM.Audit on /vms, and what the table needs on the storage and on the bridge. Sys.Audit on the
    node is each test's to grant."""
    certificate = (served["cert"], served["key"])
    for base in fake_fleet(directory, engine=True, certificate=certificate, enforce=True, **kwargs):
        engine_grant(base, "/vms", "VM.Audit")
        engine_grant(base, "/storage/local-lvm", ",".join(sorted(privileges.needed("storage"))))
        engine_grant(base, "/sdn/zones/localnetwork/vmbr0", ",".join(sorted(privileges.needed("bridge"))))
        token_id, secret = engine_token(directory)
        yield Fleet(base, directory, Token(token_id, Secret(secret)), served)


@pytest.fixture
def fleet(tmp_path, pki):
    yield from _fleet(tmp_path, pki)


def _members(fleet: Fleet, *vmids: int) -> None:
    """vmids made members of the engine's pool, as init's migration or a clone into it makes them."""
    _pve(fleet.base, "PUT", "pools", poolid=ENGINE_POOL, vms=",".join(map(str, vmids)))


def _file(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text)
    return path


# ------------------------------------------------------------------------------------------------ the reads ----


def test_the_first_request_is_the_version_asked_once_with_the_engine_s_token(fleet) -> None:
    with fleet.client() as client:
        assert client.version() == answers.Version("9.2.21", "9.2", 9)
        client.resources("vm")
        client.next_id()
    assert fleet.paths() == ["version", "cluster/resources", "cluster/nextid"]
    assert {entry["token"] for entry in fleet.asked()} == {ENGINE_TOKEN}


def test_the_listing_reads_each_guest_with_its_pool_and_frozen(fleet) -> None:
    _members(fleet, 9010, 9011)
    with fleet.client() as client:
        listed = {entry.vmid: entry for entry in client.resources("vm")}
    alpha = listed[9010]
    assert (alpha.type, alpha.node, alpha.name, alpha.status, alpha.pool) == (
        "qemu",
        "pve",
        "alpha",
        "running",
        "aw-fleet",
    )
    assert alpha.tags == ("agent-warehouse",) and not alpha.template and alpha.maxmem and alpha.maxcpu
    assert listed[9012].status == "stopped" and listed[9012].pool == ""
    assert listed[9000].template and listed[9003].type == "lxc"
    # A node that is down: its guests listed with neither name nor template, their status unknown.
    assert (listed[8001].node, listed[8001].status, listed[8001].name) == ("pve2", "unknown", "")
    assert isinstance(alpha.entry, types.MappingProxyType) and alpha.entry["vmid"] == 9010
    with pytest.raises(dataclasses.FrozenInstanceError):
        alpha.status = "stopped"  # type: ignore[misc]
    with pytest.raises(TypeError):
        alpha.entry["status"] = "stopped"  # type: ignore[index]


def test_the_listing_of_each_other_kind(fleet) -> None:
    with fleet.client() as client:
        pools = client.resources("pool")
        storages = client.resources("storage")
        nodes = client.resources("node")
    assert ("pool", ENGINE_POOL) in {(entry.type, entry.id.removeprefix("/pool/")) for entry in pools}
    assert [(entry.type, entry.node, entry.storage) for entry in storages] == [("storage", "pve", "local-lvm")]
    assert {(entry.node, entry.status) for entry in nodes} == {("pve", "online"), ("pve2", "offline")}
    assert [entry["params"] for entry in fleet.asked()][1:] == [{"type": "pool"}, {"type": "storage"}, {"type": "node"}]


def test_the_engine_s_pool_with_its_members(fleet) -> None:
    _members(fleet, 9010, 9011)
    with fleet.client() as client:
        pool = client.pool(ENGINE_POOL)
    assert pool.poolid == ENGINE_POOL and pool.comment == "the fake fleet of the new engine"
    assert [(member.vmid, member.name) for member in pool.members] == [(9010, "alpha"), (9011, "beta")]
    assert fleet.asked()[-1]["params"] == {"poolid": ENGINE_POOL}


def test_a_vm_s_config_with_its_digest_identity_and_mac(fleet) -> None:
    raw = _pve(fleet.base, "GET", "nodes/pve/qemu/9010/config")["data"]
    with fleet.client() as client:
        config = client.config("pve", 9010)
    assert (config.node, config.vmid, config.name, config.tags) == ("pve", 9010, "alpha", ("agent-warehouse",))
    assert config.digest == raw["digest"] and len(config.digest) == 40 and "digest" not in config.values
    assert config.identity == proxmox.smbios_uuid(raw) != ""
    assert config.mac == proxmox.net_mac(raw["net0"]) and config.lock == "" and not config.template
    assert config.values["ciuser"] == "dev"


def test_a_vm_whose_clone_still_runs_is_read_locked_and_another_nic_has_no_mac(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/task-polls", polls=2)
    _pve(fleet.base, "POST", "nodes/pve/qemu/9000/clone", newid=9020, name="delta")
    with fleet.client() as client:
        assert client.config("pve", 9020).lock == "clone"
        assert client.config("pve", 9010, "net1").mac == "" and client.config("pve", 9000).template


def test_a_vm_s_own_state_now(fleet) -> None:
    with fleet.client() as client:
        running, stopped = client.current("pve", 9010), client.current("pve", 9012)
    assert (running.status, running.qmpstatus, running.name) == ("running", "running", "alpha") and running.uptime
    assert (stopped.status, stopped.uptime) == ("stopped", 0)


def test_the_next_free_vmid(fleet) -> None:
    with fleet.client() as client:
        found = client.next_id()
    assert found == int(_pve(fleet.base, "GET", "cluster/nextid")["data"]) == 9001


def test_a_node_s_status_with_sys_audit_and_denied_without(fleet) -> None:
    with fleet.client() as client:
        with pytest.raises(Denied) as denied:
            client.node_status("pve")
        assert denied.value.status == 403
        engine_grant(fleet.base, "/nodes/pve", "Sys.Audit")
        node = client.node_status("pve")
    assert (node.node, node.cpus, node.memory_total, node.memory_used, node.uptime) == (
        "pve",
        16,
        64 * GIB,
        24 * GIB,
        86400,
    )


def test_a_task_s_status_and_log_ended_running_and_failed(fleet) -> None:
    # Tasks today's token made: another's task is read with Sys.Audit on its node, which init grants.
    engine_grant(fleet.base, "/nodes/pve", "Sys.Audit")
    ended = _pve(fleet.base, "POST", "nodes/pve/qemu/9012/status/start")["data"]
    _pve(fleet.base, "PUT", "fake/task-polls", polls=1)
    running = _pve(fleet.base, "POST", "nodes/pve/qemu/9011/status/shutdown")["data"]
    _pve(fleet.base, "PUT", "fake/task-polls", polls=0)
    _pve(fleet.base, "PUT", "fake/task-fail", error="start failed: \x1b[31mno\x1b[0m\x07 room")
    failed = _pve(fleet.base, "POST", "nodes/pve/qemu/9012/status/start")["data"]
    with fleet.client() as client:
        assert client.task_status("pve", ended).ok and client.task_log("pve", ended).lines == ("TASK OK",)
        assert client.task_status("pve", running).running and client.task_log("pve", running).lines == ()
        status, log = client.task_status("pve", failed), client.task_log("pve", failed)
    assert (status.status, status.ok, status.exitstatus) == ("stopped", False, "start failed: no room")
    assert log.lines == ("TASK ERROR: start failed: no room",) and log.start == 0
    assert fleet.asked()[-1]["params"] == {"start": "0", "limit": "50"}


def test_a_node_s_storages_and_bridges_as_init_asks_them(fleet) -> None:
    with fleet.client() as client:
        (storage,) = client.storages("pve")
        (bridge,) = client.bridges("pve")
    assert (storage.storage, storage.type, storage.content) == ("local-lvm", "lvmthin", ("images", "rootdir"))
    assert storage.active and storage.enabled and not storage.shared and storage.avail == 412 * GIB
    assert (bridge.iface, bridge.type, bridge.active, bridge.autostart) == ("vmbr0", "bridge", True, True)
    assert [entry["params"] for entry in fleet.asked()][1:] == [
        {"content": "images", "enabled": "1"},
        {"type": "any_local_bridge"},
    ]


def test_the_token_s_permissions_on_the_pool(fleet) -> None:
    with fleet.client() as client:
        held = client.permissions(f"/pool/{ENGINE_POOL}")
    assert held.path == f"/pool/{ENGINE_POOL}" and set(held.privileges) == privileges.needed("pool")
    assert all(held.privileges.values())


def test_any_other_read_status_adds_its_data_frozen(fleet) -> None:
    with fleet.client() as client:
        history = client.read("nodes/pve/qemu/9010/rrddata", {"timeframe": "day", "cf": "AVERAGE"})
    assert history.path == "nodes/pve/qemu/9010/rrddata" and len(history.data) == 1440
    assert isinstance(history.data, tuple) and isinstance(history.data[0], types.MappingProxyType)
    assert fleet.asked()[-1]["params"] == {"timeframe": "day", "cf": "AVERAGE"}


@pytest.mark.parametrize(
    ("read", "args"),
    [
        ("config", ("../pve", 9010)),
        ("config", ("pve", 99)),
        ("config", ("pve", True)),
        ("config", ("pve", 9010, "eth0")),
        ("current", ("pve/qemu", 9010)),
        ("pool", ("../access",)),
        ("resources", ("all",)),
        ("task_status", ("pve", "UPID:pve:1/../../version")),
        ("task_status", ("pve", "UPID:pve:00000001:00000000:00000000:qm/x:1:root@pam:")),
        ("task_log", ("pve", "UPID:pve:00000001:00000000:00000000:qmstart:1:root/../x@pam:")),
        ("task_log", ("pve", "UPID:pve:00000001:00000000:00000000:qm?x:1:root@pam:")),
        ("storages", ("pve", "images&enabled=0")),
        ("permissions", ("vms",)),
        ("read", ("../version",)),
        ("read", ("nodes/pve/qemu/9010/rrddata?cf=MAX",)),
        ("read", ("nodes//pve",)),
    ],
)
def test_a_name_or_id_that_is_not_one_proxmox_gives_is_refused_before_anything_is_sent(fleet, read, args) -> None:
    with fleet.client() as client, pytest.raises((ValueError, TypeError)):
        getattr(client, read)(*args)
    assert fleet.asked() == []


# ------------------------------------------------------------------------------------ what the host keeps ----


def _anchored(fleet: Fleet, which: str) -> pin.Pin:
    pem = fleet.pki[which]
    return pin.kept(pem, trust.pem_fingerprint(pem) if which == "node" else "")


@pytest.mark.parametrize("which", ["node", "ca"])
def test_an_anchor_of_the_node_s_certificate_or_of_its_ca_verifies_the_host(fleet, which) -> None:
    pinned = _anchored(fleet, which)
    assert pinned.anchored and bool(pinned.fingerprint) is (which == "node")
    with fleet.client(pinned) as client:
        assert client.next_id() == 9001
    assert fleet.paths() == ["version", "cluster/nextid"]


def test_with_no_anchor_the_computer_s_file_that_holds_the_host_s_root_verifies_it(fleet, tmp_path) -> None:
    pinned = pin.kept(None, tls_ca_file=_file(tmp_path, "computer.pem", fleet.pki["ca"]))
    assert not pinned.anchored and not pinned.fingerprint
    with fleet.client(pinned) as client:
        assert client.version().major == 9


def test_the_context_checks_the_name_strictly_on_every_python_but_where_a_fingerprint_names_the_certificate(
    pki, tmp_path
) -> None:
    computer = _file(tmp_path, "c.pem", pki["ca"])
    named = [pin.context(pki["node"]), pin.context(pki["ca"]), pin.context(None, computer)]
    named += [pin.kept(pki["node"]).context, pin.kept(pki["ca"]).context, pin.kept(None, tls_ca_file=computer).context]
    fingerprinted = pin.kept(pki["node"], trust.pem_fingerprint(pki["node"])).context
    for made in [*named, fingerprinted]:
        # Always checked: a certificate required, strict, on every Python.
        assert made.verify_mode == ssl.CERT_REQUIRED
        assert made.verify_flags & ssl.VERIFY_X509_STRICT and made.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
        assert made.keylog_filename is None
    assert all(made.check_hostname is True for made in named) and fingerprinted.check_hostname is False
    with pytest.raises(ValueError):
        pin.context("no certificate here")


def test_a_node_s_certificate_with_its_fingerprint_reaches_the_host_at_an_address_it_does_not_name(
    tmp_path, misnamed_pki
) -> None:
    """A host reached at a tailnet's address, or through a forwarded port: the fingerprint names the one certificate,
    so its name adds nothing, and the anchor still verifies it."""
    for fleet in _fleet(tmp_path, misnamed_pki):
        with fleet.client(_anchored(fleet, "node")) as client:
            assert client.next_id() == 9001
        assert fleet.paths() == ["version", "cluster/nextid"]


MISNAMED_KEPT = ["the node's certificate, no fingerprint", "its CA", "no anchor"]


@pytest.mark.parametrize("kept", MISNAMED_KEPT)
def test_a_certificate_that_does_not_name_the_address_is_refused_with_what_to_change_and_nothing_sent(
    tmp_path, misnamed_pki, kept
) -> None:
    """The name check is a behaviour: everything verifies but the name. The remedy is the address, or the node's own
    certificate kept, never the certificate's roots: not tls_ca_file."""
    for fleet in _fleet(tmp_path / "fleet", misnamed_pki):
        pinned = {
            "the node's certificate, no fingerprint": pin.kept(misnamed_pki["node"]),
            "its CA": pin.kept(misnamed_pki["ca"]),
            "no anchor": pin.kept(None, tls_ca_file=_file(tmp_path, "computer.pem", misnamed_pki["ca"])),
        }[kept]
        with fleet.client(pinned) as client, pytest.raises(Unverified) as refused:
            client.version()
        assert fleet.asked() == []
    said = str(refused.value)
    assert refused.value.code in MISNAMED and refused.value.anchored is (kept != "no anchor")
    assert "does not name the address it was reached at, 127.0.0.1, the one the host's record gives" in said
    assert "the node's own name" in said and "aw init keep the node's own certificate" in said
    assert "tls_ca_file" not in said and "not the one kept" not in said


def _wrong(fleet: Fleet, other: dict, tmp_path: Path, kept: str) -> pin.Pin:
    """A pin of what the host does not keep: another node's certificate or CA as the anchor, the right certificate
    with another's fingerprint recorded, or no anchor and a computer's file that holds no root of the host's."""
    if kept == "another node's certificate":
        return pin.kept(other["node"], trust.pem_fingerprint(other["node"]))
    if kept == "another CA":
        return pin.kept(other["ca"])
    if kept == "another fingerprint":
        return pin.kept(fleet.pki["node"], trust.pem_fingerprint(other["node"]))
    if kept == "certifi's file":
        return pin.kept(None)
    return pin.kept(None, tls_ca_file=_file(tmp_path, "computer.pem", other["ca"]))


WRONG = ["another node's certificate", "another CA", "another fingerprint", "certifi's file", "a file of another CA"]


@pytest.mark.parametrize("kept", WRONG)
def test_a_certificate_that_is_not_what_the_host_keeps_has_nothing_sent(fleet, other_pki, tmp_path, kept) -> None:
    pinned = _wrong(fleet, other_pki, tmp_path, kept)
    with fleet.client(pinned) as client:
        with pytest.raises(Unverified) as refused:
            client.config("pve", 9010)
        with pytest.raises(Unverified):
            client.resources("vm")
    assert fleet.asked() == []
    said = str(refused.value)
    # Told by OpenSSL's verify code, never its words: none of these is a name's, and each names aw init, which reads the
    # host's own certificate and asks before it keeps one.
    assert isinstance(refused.value, Unverified) and refused.value.code not in MISNAMED and "aw init" in said
    if pinned.anchored:
        assert "not the one kept for it" in said and "tls_ca_file" not in said
    else:
        # The issuer unknown with no anchor kept: a root of the person's own second, never alone, since the root of a
        # Proxmox host's certificate is almost always its own CA, which tls_ca_file would trust for every vendor too.
        assert refused.value.code in UNKNOWN_ISSUER and said.index("aw init") < said.index("tls_ca_file")


@pytest.mark.parametrize(
    ("code", "anchored", "names"),
    [
        (64, True, "does not name the address"),
        (62, False, "does not name the address"),
        (10, True, "has expired"),
        (10, False, "has expired"),
        (9, True, "is not valid yet"),
        (9, False, "is not valid yet"),
        (20, False, "tls_ca_file"),
        (0, True, "not the one kept for it"),
    ],
    ids=[
        "ip mismatch",
        "hostname mismatch",
        "expired",
        "expired, no anchor",
        "not yet valid",
        "not yet valid, no anchor",
        "unknown issuer, no anchor",
        "the pin",
    ],
)
def test_the_remedy_is_told_by_the_verify_code_and_never_by_the_words(code, anchored, names) -> None:
    # The words of another case each time: only the code may decide.
    said = str(Unverified("IP address mismatch, unable to get local issuer certificate", code, anchored, "pve.lan"))
    dated = code in (EXPIRED, NOT_YET_VALID)
    assert names in said
    # aw init reads the host's certificate wherever that is the remedy: never for a host that keeps no anchor and
    # renews its certificate.
    assert ("aw init" in said) is (anchored or not dated)
    assert ("tls_ca_file" in said) is (not anchored and code in UNKNOWN_ISSUER)
    assert ("the node's own name" in said) is (code in MISNAMED)
    assert ("renew it" in said) is dated and ("not the one kept" in said) is (anchored and code == 0)
    # Not valid yet: a clock is behind, and this computer's time now is said, to compare with the certificate's.
    clock = re.search(r"this computer's clock says (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC", said)
    assert bool(clock) is (code == NOT_YET_VALID)
    if clock:
        now = time.time()
        assert abs(calendar.timegm(time.strptime(clock.group(1), "%Y-%m-%d %H:%M:%S")) - now) < 5


@pytest.mark.parametrize("dates", ["expired", "not yet valid"])
@pytest.mark.parametrize("which", ["node", "ca"], ids=["the node's own certificate", "its CA"])
def test_a_kept_certificate_out_of_its_dates_is_said_so_and_nothing_is_sent(tmp_path, dates, which) -> None:
    """review/code/B22.md 4: a node's certificate that ended yesterday, or begins tomorrow, kept as the host's anchor
    or under its CA: OpenSSL's code says which, and the sentence says the certificate's dates were passed and what to
    renew, never that it is not the one kept."""
    served = proxmox_pki(tmp_path / "pki", key_usage=True, expired=dates == "expired", not_yet=dates == "not yet valid")
    for fleet in _fleet(tmp_path / "fleet", served):
        with fleet.client(_anchored(fleet, which)) as client, pytest.raises(Unverified) as refused:
            client.version()
        assert fleet.asked() == []
    said = str(refused.value)
    assert refused.value.code == (EXPIRED if dates == "expired" else NOT_YET_VALID), refused.value.code
    assert ("has expired" if dates == "expired" else "is not valid yet") in said
    assert "renew it" in said and "aw init reads the host's new one" in said and "not the one kept" not in said


@pytest.mark.parametrize("kept", WRONG)
def test_with_the_fake_answering_no_request_without_a_token_a_wrong_certificate_still_has_the_token_never_leave(
    fleet, other_pki, tmp_path, kept
) -> None:
    """What answers in the host's place may tell a probe with no token from the request that carries one, and say
    nothing to the probe: the client sends no probe, and the token goes only once the certificate verified."""
    _pve(fleet.base, "PUT", "fake/tokenless", answer="none")
    with fleet.client(_wrong(fleet, other_pki, tmp_path, kept)) as client, pytest.raises(Unverified):
        client.version()
    assert [entry for entry in _pve_requests(fleet.directory) if entry["token"] == ENGINE_TOKEN] == []
    assert fleet.asked() == []


def test_a_ca_with_no_key_usage_is_refused_on_every_python_and_its_node_s_certificate_verifies(tmp_path) -> None:
    """A CA as Proxmox VE 8 made one, and VE 9 before pve-cluster 9.1.8, has no keyUsage: strict on every Python, it is
    no anchor, and the host's anchor is its node's own certificate, with its fingerprint (init's to keep, card E3)."""
    old = proxmox_pki(tmp_path / "old-pki", key_usage=False)
    for fleet in _fleet(tmp_path / "fleet", old):
        with fleet.client(pin.kept(old["ca"])) as client, pytest.raises(Unverified):
            client.version()
        with fleet.client(pin.kept(old["node"], trust.pem_fingerprint(old["node"]))) as client:
            assert client.version().major == 9
        assert fleet.paths() == ["version"]


def test_a_fingerprint_is_kept_only_beside_an_anchor_and_only_as_openssl_prints_one(pki) -> None:
    with pytest.raises(ValueError):
        pin.kept(None, trust.pem_fingerprint(pki["node"]))
    for given in ("AB" * 32, "ab:cd", trust.pem_fingerprint(pki["node"]) + "\n"):
        with pytest.raises(ValueError):
            pin.kept(pki["node"], given)
        with pytest.raises(ValueError):
            pin.check(given)
    lower = trust.pem_fingerprint(pki["node"]).lower()
    assert pin.kept(pki["node"], lower).fingerprint == lower.upper()
    held = pin.check(lower)
    held(ssl.PEM_cert_to_DER_cert(pki["node"]))
    with pytest.raises(pin.Mismatch):
        held(ssl.PEM_cert_to_DER_cert(pki["ca"]))


def test_no_connection_of_the_client_logs_its_session_s_keys_whatever_sslkeylogfile_names(
    fleet, tmp_path, monkeypatch
) -> None:
    """The standard library's ready-made context names the file SSLKEYLOGFILE gives unless Python ignores the
    environment, as pytest does not: it opens that file, and writes its header there, as it is made. Each of pin's
    contexts is made by hand, today's trust.anchored_context's too (review/code/B22.md 5), so that the file is never
    opened - one that cannot be, or a FIFO nobody reads, would refuse or hold the client - and no key of the one
    connection that carries the token is ever written."""
    keys = tmp_path / "keys.log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keys))
    computer = _file(tmp_path, "computer.pem", fleet.pki["ca"])
    for pinned in (_anchored(fleet, "node"), _anchored(fleet, "ca"), pin.kept(None, tls_ca_file=computer)):
        assert pinned.context.keylog_filename is None
        with fleet.client(pinned) as client:
            assert client.next_id() == 9001
    assert not keys.exists()
    # Named in a folder that is not there, it changes nothing either: the file is never opened.
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "no-such-folder" / "keys.log"))
    assert pin.context(fleet.pki["node"]).keylog_filename is None and trust.anchored_context(fleet.pki["ca"])


def test_through_the_proxy_today_s_rule_chooses_the_token_stays_in_the_tunnel(fleet) -> None:
    tunnel = _Tunnel()
    try:
        url = f"{fleet.base}/api2/json"
        chosen = proxy.choose("environment", {"HTTPS_PROXY": tunnel.url}, url)
        assert chosen == tunnel.url
        with fleet.client(_anchored(fleet, "node"), proxy=chosen) as client:
            assert client.next_id() == 9001
        port = fleet.base.rsplit(":", 1)[1]
        assert {asked.split(b"\r\n")[0] for asked in tunnel.asked} == {f"CONNECT 127.0.0.1:{port} HTTP/1.1".encode()}
        assert tunnel.carried[:1] == b"\x16" and fleet.token.secret.reveal().encode() not in tunnel.carried
        assert b"PVEAPIToken" not in tunnel.carried
    finally:
        tunnel.close()
    assert fleet.paths() == ["version", "cluster/nextid"]


# ----------------------------------------------------------------------------------- what Proxmox answers ----


def test_a_redirect_is_an_answer_and_its_address_is_never_asked(fleet) -> None:
    _pve(fleet.base, "PUT", "fake/redirect", location=f"{fleet.base}/api2/json/fake/elsewhere")
    with fleet.client() as client, pytest.raises(Refused) as refused:
        client.version()
    assert refused.value.status == 302 and "never followed" in str(refused.value)
    assert fleet.paths() == ["version"]
    assert [entry for entry in _pve_requests(fleet.directory) if entry["path"].endswith("/elsewhere")] == []


class _SlowVersion:
    """A transport that answers /version only once its call's deadline has passed: the transport's TimedOut, as it ends
    a GET with no answer in time. Each request's path kept."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.lock = threading.Lock()

    def request(self, method, path, deadline=None, **kwargs) -> Answer:
        with self.lock:
            self.asked.append(path)
        time.sleep(deadline if deadline is not None else 5.0)
        raise TimedOut("no answer within the call's deadline")


def test_two_first_calls_at_once_each_ask_the_version_within_their_own_deadline(pki) -> None:
    """review/code/B22.md 6: a new client's first two calls at once, two VMs' first polls, each held to a second. Each
    ends within its own second, never waiting for the other's request first, and each asks the version itself."""
    client = Proxmox("https://127.0.0.1:9", pin.kept(pki["ca"]), Token(f"{ENGINE_USER}!{ENGINE_TOKEN}", Secret(CANARY)))
    client._transport = slow = _SlowVersion()
    took: dict[str, float] = {}
    began = threading.Barrier(2)

    def first(name: str) -> None:
        began.wait(5)
        start = time.monotonic()
        with pytest.raises(TimedOut):
            client.version(1.0)
        took[name] = time.monotonic() - start

    threads = [threading.Thread(target=first, args=(name,), daemon=True) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert set(took) == {"first", "second"} and all(1.0 <= seconds < 1.5 for seconds in took.values()), took
    assert slow.asked == ["version", "version"]


def test_a_host_below_9_is_refused_before_any_other_request(tmp_path, pki) -> None:
    for fleet in _fleet(tmp_path, pki, version="8.2.4"):
        with fleet.client() as client:
            with pytest.raises(TooOld) as old:
                client.resources("vm")
            with pytest.raises(TooOld):
                client.config("pve", 9010)
            with pytest.raises(TooOld):
                client.version()
        assert "Proxmox VE 8.2.4" in str(old.value) and old.value.version == "8.2.4"
        assert fleet.paths() == ["version"]


def test_a_node_that_is_down_fails_its_own_reads_alone_and_the_listing_still_answers(fleet) -> None:
    with fleet.client() as client:
        for read, args in [("config", ("pve2", 8001)), ("current", ("pve2", 8001)), ("node_status", ("pve2",))]:
            with pytest.raises(Refused) as refused:
                getattr(client, read)(*args)
            assert refused.value.status == 595 and "No route to host" in str(refused.value)
        listed = {entry.vmid: entry for entry in client.resources("vm")}
        assert listed[8001].node == "pve2" and listed[9010].status == "running"
        assert client.config("pve", 9010).name == "alpha"


def test_a_401_and_a_403_are_denied(fleet) -> None:
    wrong = Token(fleet.token.id, Secret(CANARY))
    with fleet.client(token=wrong) as client, pytest.raises(Denied) as unknown:
        client.version()
    assert unknown.value.status == 401
    # A storage the token holds nothing on.
    _pve(fleet.base, "PUT", "fake/storages", node="pve", storages="local-lvm:lvmthin,nas:nfs")
    with fleet.client() as client, pytest.raises(Denied) as refused:
        client.read("nodes/pve/storage/nas/content")
    assert refused.value.status == 403


def test_a_500_whose_reason_holds_401_is_refused_not_denied(fleet) -> None:
    with fleet.client() as client, pytest.raises(Refused) as refused:
        client.config("pve", 401)
    assert refused.value.status == 500 and "401" in refused.value.reason
    assert not isinstance(refused.value, Denied)


class _Answers:
    """A transport that answers each request with the next of answers, for the cases the fake does not make."""

    def __init__(self, *given: Answer) -> None:
        self.given = list(given)

    def request(self, *args, **kwargs) -> Answer:
        return self.given.pop(0)


VERSION = Answer(200, (), b'{"data": {"version": "9.2.21", "release": "9.2"}}', "OK")


@pytest.mark.parametrize(
    ("given", "raised"),
    [
        (Answer(401, (), b"", "anything at all"), Denied),
        (Answer(403, (), b'{"data": null}', "no privilege named"), Denied),
        (Answer(500, (), b'{"data": null}', "401 Unauthorized: Permission check failed"), Refused),
        (Answer(500, (), b'{"data": null}', "authentication failure"), Refused),
        (Answer(404, (), b"", ""), Refused),
        (Answer(301, (("Location", "https://elsewhere/"),), b"", "Moved"), Refused),
        (Answer(200, (), b"no JSON", "OK"), Malformed),
        (Answer(200, (), b'{"nodata": 1}', "OK"), Malformed),
        (Answer(200, (), b'{"data": NaN}', "OK"), Malformed),
        (Answer(200, (), b'{"data": ' + b"[" * 100000 + b"]" * 100000 + b"}", "OK"), Malformed),
        (Answer(200, (), b'{"data": {"name": "alpha"}}', "OK"), Malformed),
    ],
    ids=[
        "401",
        "403",
        "500 naming 401",
        "500 naming authentication",
        "404",
        "301",
        "no JSON",
        "no data",
        "NaN",
        "too deep",
        "a config with no digest",
    ],
)
def test_what_an_answer_is_decided_by_its_status_alone(fleet, given, raised) -> None:
    client = fleet.client()
    client._transport = _Answers(VERSION, given)
    with pytest.raises(raised) as error:
        client.config("pve", 9010)
    assert type(error.value) is raised


def _nested(depth: int) -> list:
    """A list that holds a list, depth deep, made without recursion."""
    value: list = []
    for _ in range(depth):
        value = [value]
    return value


def test_an_answer_nested_deeper_than_any_of_proxmox_s_is_malformed_never_a_recursion_error(fleet) -> None:
    """json.loads, in C, takes an answer nested past Python's recursion limit: what keeps it frozen stops first."""
    deep = b"[" * 900 + b"]" * 900
    client = fleet.client()
    client._transport = _Answers(VERSION, Answer(200, (), b'{"data": ' + deep + b"}", "OK"))
    with pytest.raises(Malformed):
        client.read("nodes/pve/qemu/9010/pending")
    for parse in (
        lambda v: answers.read(v, "x"),
        lambda v: answers.resource({"vmid": 9010, "deep": v}),
        lambda v: answers.config({"digest": "0" * 40, "deep": v}, "pve", 9010, "net0"),
    ):
        with pytest.raises(Malformed):
            parse(_nested(900))
        with pytest.raises(Malformed):
            parse(_nested(answers.MOST_DEPTH + 1))
    # As deep as MOST_DEPTH lets is read as it came.
    assert answers.read(_nested(answers.MOST_DEPTH - 1), "x").data


@pytest.mark.parametrize(
    ("parse", "value"),
    [
        (lambda v: answers.version(v), {"version": "nine"}),
        (lambda v: answers.next_id(v), "99"),
        (lambda v: answers.next_id(v), "abc"),
        (lambda v: answers.task_status(v, "pve", "UPID"), {"status": "done"}),
        (lambda v: answers.current(v, "pve", 9010), {"qmpstatus": "running"}),
        (lambda v: answers.resources(v), {"not": "a list"}),
        (lambda v: answers.pool(v, "aw-fleet"), [{"poolid": "another"}]),
    ],
)
def test_an_answer_without_what_its_record_needs_is_malformed(parse, value) -> None:
    with pytest.raises(Malformed):
        parse(value)


def test_proxmox_s_loose_json_is_read_as_it_comes() -> None:
    entry = answers.resource({"vmid": "9010", "template": "1", "maxmem": "1024", "tags": "b;a,c", "uptime": 5.0})
    assert (entry.vmid, entry.template, entry.maxmem, entry.tags, entry.uptime) == (
        9010,
        True,
        1024,
        ("b", "a", "c"),
        5,
    )
    assert answers.resource({"vmid": True, "template": 0}).vmid is None
    assert answers.next_id(9013) == answers.next_id("9013") == 9013


# ------------------------------------------------------------------------------------------- the token ----


def test_pveapitoken_is_written_in_one_module_of_the_new_engine() -> None:
    writing = [_rel(path) for path in _new_engine() if "PVEAPIToken" in path.read_text()]
    assert writing == [_rel(CLIENT)]


def _returns_the_token(tree: ast.AST) -> list[int]:
    """The lines of tree's returns that name the token, or its secret."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Return | ast.Yield | ast.YieldFrom)
        and node.value is not None
        and any(isinstance(n, ast.Attribute) and n.attr in ("_token", "secret") for n in ast.walk(node.value))
    ]


def test_no_method_of_the_client_returns_the_token() -> None:
    assert _returns_the_token(ast.parse(CLIENT.read_text())) == []
    assert _returns_the_token(ast.parse("def f(self):\n    return self._token.secret")) == [2]


def _holds_a_secret(value, seen=None) -> bool:
    """Whether value, a read's record, holds a Secret or a Token anywhere."""
    if isinstance(value, Secret | Token):
        return True
    if dataclasses.is_dataclass(value):
        return any(_holds_a_secret(getattr(value, field.name)) for field in dataclasses.fields(value))
    if isinstance(value, dict | types.MappingProxyType):
        return any(_holds_a_secret(one) for one in value.values())
    if isinstance(value, tuple | list):
        return any(_holds_a_secret(one) for one in value)
    return False


def test_no_read_returns_the_token_or_its_secret(fleet) -> None:
    secret = fleet.token.secret.reveal()
    _members(fleet, 9010)
    engine_grant(fleet.base, "/nodes/pve", "Sys.Audit")
    upid = _pve(fleet.base, "POST", "nodes/pve/qemu/9012/status/start")["data"]
    with fleet.client() as client:
        got = [
            client.version(),
            client.resources("vm"),
            client.pool(ENGINE_POOL),
            client.config("pve", 9010),
            client.current("pve", 9010),
            client.next_id(),
            client.node_status("pve"),
            client.task_status("pve", upid),
            client.task_log("pve", upid),
            client.storages("pve"),
            client.bridges("pve"),
            client.permissions("/vms/9010"),
            client.read("nodes/pve/qemu/9010/pending"),
        ]
        shown = repr(client) + repr(fleet.token) + str(fleet.token)
    assert not any(_holds_a_secret(one) for one in got)
    assert secret not in repr(got) and secret not in shown and fleet.token.id in shown


def _errors(fleet: Fleet, other: dict, tmp_path: Path) -> list[BaseException]:
    """One error of each kind the client raises, each from a request that carried the token or would have."""
    raised = []

    def caught(token: Token, read, *args, pinned: pin.Pin | None = None) -> None:
        with Proxmox(fleet.base, pinned or pin.kept(fleet.pki["ca"]), token) as client:
            try:
                getattr(client, read)(*args)
            except Exception as error:  # noqa: BLE001 - each is what the test reads
                raised.append(error)

    caught(Token(fleet.token.id, Secret(CANARY)), "version")
    caught(Token(fleet.token.id, Secret(f"{CANARY}\r\nX-Injected: 1")), "version")
    caught(fleet.token, "config", "pve", 401)
    caught(fleet.token, "node_status", "pve")
    caught(fleet.token, "version", pinned=pin.kept(other["ca"]))
    caught(fleet.token, "version", pinned=pin.kept(fleet.pki["node"], trust.pem_fingerprint(other["node"])))
    caught(fleet.token, "version", pinned=pin.kept(None, tls_ca_file=_file(tmp_path, "other.pem", other["ca"])))
    return raised


def test_no_error_of_the_client_says_the_token(fleet, other_pki, tmp_path, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)
    errors = _errors(fleet, other_pki, tmp_path)
    assert [type(error) for error in errors] == [Denied, NotMade, Refused, Denied, Unverified, Unverified, Unverified]
    for error in errors:
        said = "\n".join(_said(error, caplog, capsys))
        assert CANARY not in said and fleet.token.secret.reveal() not in said, type(error)
        assert error.__cause__ is None and error.__context__ is None
