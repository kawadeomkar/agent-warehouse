"""A VM made by the new engine: lib/claude_vm/engine/create.py, and deploy's handler, lib/claude_vm/engine/deploy.py
(card G3; docs/architecture/engine.md §16.4).

Without the fake: the one decision, need_configuration, by its cases and reading no ownership; the settings call's keys,
today's "Apply hardware and cloud-init settings" (roles/proxmox_vm/tasks/present.yml) read from the role, with ciupgrade
only under its opt-in, the console password only where the vault holds one, the memory's minimum by today's rule and the
parts a sized VM keeps on a repair; the one reader of a size, so that 16g and 16G are one value and a refusal says no
value typed; each stage of deploy and each option no card has built yet refused with the card that builds it; the
first-use mode named in engine/ by step 8 alone; a lock another run holds refused by cli/main.py in Held's sentence,
with exit status 1; the route's and the host's repr holding nothing of the run's values; the console password read from
the vault; a clone whose answer was lost settled, with a stand-in client, before anything is cloned again, and its VM
failed where the token cannot read its VMID.

On a whole fleet folder (tests/unit/fleet_fixture.py) against the fake Proxmox (tests/molecule/fake_pve_api.py), the
engine's token held to its table of privileges, the fake's template given this engine's record and made a member of the
pool, and each VM a run makes a stand-in machine (tests/unit/aw_machine.py): its SMBIOS UUID queued for the next clone
and its guest agent reporting the machine's address, so the run reaches the stand-in and takes its key at first use. Two
VMs made in one run, each cloned in full onto the fleet's storage into the pool, recorded, labelled, set, resized,
started, its key filed under its identity; then a second run that sends nothing but GETs. A VM no key and no console
password would reach, and a template with no cloud-init drive, refused before anything is sent; a VMID another's guest
took asked for again, the guest left as it was; a clone whose answer was lost sent once, the token reading every VM or
the pool's alone; a start that failed, a resize or a start refused at its call, each resumed by the next run, and a
running VM resumed never started again; a clone left in its first form refused to every later run, saying to delete it;
a Ctrl-C during the clone held until it lands and is recorded; a Ctrl-C during the agent's wait ending the VM stopped in
the job's record, and one while the settings call's answer is held (the fake's hold) sending nothing after it, the next
run resizing the VM (the first checkpoint's findings 1 and 2); a Ctrl-C during the resize's own read, and during the
read before the start, sending neither, and a second Ctrl-C or a SIGTERM at the agent's wait ending the VM stopped all
the same, by the ladder's end (review 83's findings 1 and 2); a VM with a console password and no key made; a stopped VM
left stopped, and started with --start; a size given at the create recorded in capital form, refused for the VM once it
exists and kept on a repair, which never resizes and refuses a larger disk; --repos given, added to, and refused for a
VM that follows the setting.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import threading
import time
import urllib.parse
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from aw_kit import aw_at_a_terminal, run_aw
from aw_machine import SSH_FOLDER, Machine, interfaces, machine, ssh_stand_in
from fleet_fixture import STORAGE, pinned, whole_fleet
from harness import ENGINE_POOL, ENGINE_TOKEN, REPO, _pve, _pve_requests, engine_grant, fake_fleet
from pki import proxmox_pki

from claude_vm import proxmox
from claude_vm.cli import dispatch
from claude_vm.cli.main import main
from claude_vm.engine import create, deploy, fleet, routes, template
from claude_vm.engine.record import Record
from claude_vm.fleet import hosts, opened, overrides, paths, sealed, secrets, vaultkey
from claude_vm.hosts.model import Request
from claude_vm.pve import answers, pin
from claude_vm.pve import record as block
from claude_vm.pve.client import Denied, Proxmox
from claude_vm.runs import job as jobs
from claude_vm.runs import locks
from claude_vm.schema.words import EXITS, VERBS
from claude_vm.secret import Secret
from claude_vm.vendors.transport import Unknown

CREATE = REPO / "lib" / "claude_vm" / "engine" / "create.py"
PRESENT = REPO / "roles" / "proxmox_vm" / "tasks" / "present.yml"
# The fake's template, and the VMIDs its next clones take: the lowest free from its floor (9003 is a container).
TEMPLATE = 9000
FIRST, SECOND = 9001, 9002
# fake_fleet's VM alpha, a VM of today's outside the pool, and the fake's container: VMIDs a clone is refused.
ALPHA, CONTAINER = 9010, 9003
# The image's digest a build records, which a comparison never reads.
DIGEST = "3f9a0c1e7b2d4a58" * 4
# The key this computer offers, as ~/.ssh/id_ed25519.pub holds it: vm_ssh_public_keys' default.
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGtlc3Qta2V5LW9mLXRoZS10ZXN0cy1vd24tbWFkZS11cA test@aw"
PASSWORD = "console-password-of-the-test"
# A digest of a configuration, as Proxmox gives one.
CONFIG_DIGEST = "0123456789abcdef0123456789abcdef01234567"


def _settings(**changes: object) -> dict[str, object]:
    """Every setting's value for a run against the host lab in this test's own home, its route pinned first, with
    changes over them."""
    pinned("lab")
    resolved = overrides.resolve(paths.home(REPO), "lab")
    return {**{name: found.value for name, found in resolved.items()}, **changes}


def _config(**values: object) -> answers.VmConfig:
    """A VM's configuration as the client reads one."""
    return answers.config({"digest": CONFIG_DIGEST, **values}, "pve", FIRST, "net0")


def _record(**fields: object) -> Record:
    made = {"kind": "vm", "uuid": "9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c4", "name": "delta", "stage": "unfinished"}
    return Record(**{**made, **fields})


# ---------------------------------------------------------------- the one decision ----


def test_need_configuration_is_today_s_rule_from_the_record_and_the_configuration() -> None:
    finished = _record(stage="unfinished", provisioned=True)
    set_up = _config(ciuser="dev")
    assert create.need_configuration(finished, set_up) is False
    # Each road on which it is needed: no cloud-init user, no record, the first form, not provisioned, a repair.
    assert create.need_configuration(finished, _config()) is True
    assert create.need_configuration(None, set_up) is True
    assert create.need_configuration(_record(uuid="", stage="cloning", job="0123456789abcdef"), set_up) is True
    assert create.need_configuration(_record(stage="unfinished"), set_up) is True
    assert create.need_configuration(finished, set_up, True) is True
    # Only True repairs; a stage past unfinished is no reason by itself.
    assert create.need_configuration(finished, set_up, "yes") is False
    assert create.need_configuration(_record(stage="finished", provisioned=True), set_up) is False
    with pytest.raises(TypeError):
        create.need_configuration(finished, {"ciuser": "dev"})
    with pytest.raises(TypeError):
        create.need_configuration({"provisioned": "yes"}, set_up)


def test_need_configuration_reads_no_ownership() -> None:
    [function] = [
        node
        for node in ast.walk(ast.parse(CREATE.read_text()))
        if isinstance(node, ast.FunctionDef) and node.name == "need_configuration"
    ]
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)}
    assert {"owned", "Owned", "ownership", "refusal", "owner", "Refused"} & names == set(), names


def test_no_runner_call_of_the_engine_but_step_8_s_takes_the_first_use_mode() -> None:
    """The first-use mode (a Target taken through first_use, or one whose first_use is read) is named in engine/ only
    by create.py's step 8 and the Route's method that hands it out."""
    allowed = {("create.py", "_key"), ("routes.py", "first_use")}
    found = set()
    for path in sorted((REPO / "lib" / "claude_vm" / "engine").glob("*.py")):
        tree = ast.parse(path.read_text())
        for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(function):
                named = (
                    (isinstance(node, ast.Attribute) and node.attr == "first_use")
                    or (isinstance(node, ast.Name) and node.id == "first_use")
                    or (isinstance(node, ast.keyword) and node.arg == "first_use")
                )
                if named:
                    found.add((path.name, function.name))
    assert found - allowed == set(), found
    assert ("create.py", "_key") in found


# ---------------------------------------------------------------- a clone whose answer was lost ----

JOB = "0123456789abcdef"


class _Reads:
    """A client that answers the token's privileges on /vms, the cluster's listing and one VM's configuration, read
    after read, as a lost clone's VMID reads: VM.Audit held there or not, listed or not, its configuration's lock and
    its notes, or the error its read raises."""

    def __init__(
        self, listed: bool, configs: list[tuple[str | None, str]], audit: bool = True, refused: Exception | None = None
    ) -> None:
        self.listed, self.configs, self.audit, self.refused = listed, configs, audit, refused
        self.read = self.lists = 0

    def permissions(self, path: str) -> SimpleNamespace:
        assert path == fleet.EVERY_VM
        return SimpleNamespace(privileges={fleet.AUDIT: 1} if self.audit else {})

    def resources(self, kind: str) -> list:
        self.lists += 1
        return [SimpleNamespace(vmid=FIRST, node="pve")] if self.listed else []

    def config(self, node: str, vmid: int, nic: str) -> SimpleNamespace:
        if self.refused is not None:
            raise self.refused
        lock, notes = self.configs[min(self.read, len(self.configs) - 1)]
        self.read += 1
        return SimpleNamespace(lock=lock, description=notes)


def _settling(client: _Reads) -> create.Making:
    ctx = SimpleNamespace(job=SimpleNamespace(id=JOB), cancelled=threading.Event())
    host = SimpleNamespace(client=client, nic="net0", pool=ENGINE_POOL)
    return create.Making(ctx, host, None, create.Plan(), None, "delta", "stand-in")


def _first_form(job: str) -> str:
    return block.render("", Record(kind=fleet.KIND, name="delta", stage="cloning", job=job).fields())


def test_a_clone_whose_answer_was_lost_is_settled_before_anything_is_cloned_again(monkeypatch) -> None:
    monkeypatch.setattr(create, "PAUSE", 0.0)
    # Nothing at the VMID once the listing's lag is past: nothing of this run's is there, and a VMID is asked again.
    monkeypatch.setattr(create, "LISTING_LAG", -1.0)
    assert asyncio.run(_settling(_Reads(False, [(None, "")]))._settle(FIRST)) is None
    # Read while its clone's lock holds it, with no notes yet; then this run's first form: landed, with no task to own
    # it by, so the VM fails with the way out, and nothing is cloned again.
    landed = _Reads(True, [(fleet.CLONING, ""), (fleet.CLONING, ""), (None, _first_form(JOB))])
    with pytest.raises(create.Failed) as failed:
        asyncio.run(_settling(landed)._settle(FIRST))
    assert f"VMID {FIRST}" in failed.value.sentence and "delete that VM in Proxmox" in failed.value.sentence
    assert landed.read == 3
    # Another run's clone, or another guest: not this run's, and a VMID is asked again.
    assert asyncio.run(_settling(_Reads(True, [(None, _first_form("fedcba9876543210"))]))._settle(FIRST)) is None
    # A lock that holds past the settling time: failed, saying so.
    monkeypatch.setattr(create, "SETTLING", -1.0)
    with pytest.raises(create.Failed) as failed:
        asyncio.run(_settling(_Reads(True, [(fleet.CLONING, "")]))._settle(FIRST))
    assert "still being cloned" in failed.value.sentence


def test_a_lost_clone_the_token_cannot_read_fails_its_vm_and_is_never_cloned_again(monkeypatch) -> None:
    # A clone joins the pool only once its task ends: a token that may read no VM outside the pool never sees it while
    # it runs, so nothing there would be taken for nothing, and the VM fails at once, the listing never read (review
    # 66's finding 1).
    monkeypatch.setattr(create, "PAUSE", 0.0)
    unseen = _Reads(False, [(None, "")], audit=False)
    with pytest.raises(create.Failed) as failed:
        asyncio.run(_settling(unseen)._settle(FIRST))
    said = failed.value.sentence
    assert said.startswith(f"delta: Proxmox's answer to its clone at VMID {FIRST} was lost, and the token may not read")
    assert "VM.Audit on /vms, proxmox_audit_scope: pool" in said and "nothing more is cloned" in said
    assert "delete that VM in Proxmox where it is delta, then run this again" in said
    assert (unseen.lists, unseen.read) == (0, 0)
    # Listed, but its configuration refused: failed the same way, naming why.
    refused = _Reads(True, [(None, "")], refused=Denied(403, f"GET nodes/pve/qemu/{FIRST}/config"))
    with pytest.raises(create.Failed) as failed:
        asyncio.run(_settling(refused)._settle(FIRST))
    assert "its configuration could not be read (Proxmox refused the token" in failed.value.sentence
    assert "nothing more is cloned" in failed.value.sentence


# ---------------------------------------------------------------- the settings call ----


def _todays_keys() -> set[str]:
    """The keys today's settings call sends Proxmox: the role's task's parameters, the module's own (where and as which
    VM, update) and the notes and tags, which the record and the label write in calls of their own, left out; ipconfig's
    keys, and the module's plural names as the API names them."""
    tasks = yaml.safe_load(PRESENT.read_text())
    [task] = [task for task in tasks if task.get("name") == "present | Apply hardware and cloud-init settings"]
    module = task["community.proxmox.proxmox_kvm"]
    keys = set(module) - {"node", "vmid", "name", "update", "description", "tags", "ipconfig"}
    keys |= set(module["ipconfig"])
    return {{"nameservers": "nameserver", "searchdomains": "searchdomain"}.get(key, key) for key in keys}


def test_the_settings_call_sends_today_s_keys_and_ciupgrade_only_under_its_opt_in(home) -> None:
    every = _settings(
        vm_ssh_public_keys=[KEY, KEY.replace("test@aw", "other@aw")],
        vm_nameservers=["192.0.2.53", "192.0.2.54"],
        vm_searchdomain="lab.example",
        vm_first_boot_upgrade=False,
    )
    sent = create.settings_call(every, create.Size(), Secret(PASSWORD))
    assert set(sent) == _todays_keys() | {"ciupgrade"}
    assert sent["ciupgrade"] == 0 and sent["nameserver"] == "192.0.2.53 192.0.2.54"
    assert sent["searchdomain"] == "lab.example" and sent["sshkeys"].splitlines() == every["vm_ssh_public_keys"]
    assert (sent["onboot"], sent["agent"], sent["ciuser"], sent["ipconfig0"]) == (1, "enabled=1", "dev", "ip=dhcp")
    assert isinstance(sent["cipassword"], Secret) and sent["cipassword"].reveal() == PASSWORD
    # The defaults: no password, no nameserver or search domain, the first boot's upgrade as cloud-init has it.
    plain = create.settings_call(_settings(vm_ssh_public_keys=[KEY]), create.Size(), None)
    assert set(plain) == {"onboot", "agent", "ciuser", "ipconfig0", "sshkeys", "cores", "memory", "balloon"}
    # A password and no key: made, as today.
    assert "sshkeys" not in create.settings_call(_settings(vm_ssh_public_keys=[]), create.Size(), Secret(PASSWORD))
    with pytest.raises(TypeError):
        create.settings_call(_settings(), create.Size(), PASSWORD)


def test_the_memory_s_minimum_is_today_s_and_a_sized_vm_keeps_its_size_on_a_repair(home) -> None:
    fixed = _settings(vm_memory_mb=4096, vm_memory_min_mb=4096, vm_cores=2)
    assert {key: create.settings_call(fixed, create.Size(), None)[key] for key in ("cores", "memory", "balloon")} == {
        "cores": 2,
        "memory": 4096,
        "balloon": 4096,
    }
    # A ballooning minimum below the setting's memory: kept under a size given, never above it.
    ballooning = _settings(vm_memory_mb=4096, vm_memory_min_mb=1024)
    given = create.settings_call(ballooning, create.Size.read(4, "8g"), None)
    assert (given["cores"], given["memory"], given["balloon"]) == (4, 8192, 1024)
    assert create.settings_call(fixed, create.Size.read(None, "8G"), None)["balloon"] == 8192
    # The parts a VM created with a size of its own keeps, each the run does not give; none for one never sized.
    sized = _record(provisioned=True, cores=4, memory="8G", disk="64G")
    assert create.kept_size(sized, create.Size()) == ("cores", "memory", "disk")
    assert create.kept_size(sized, create.Size.read(6)) == ("memory", "disk")
    assert create.kept_size(_record(provisioned=True), create.Size()) == ()
    assert create.kept_size(None, create.Size()) == ()
    kept = create.settings_call(fixed, create.Size(), None, create.kept_size(sized, create.Size()))
    assert {"cores", "memory", "balloon"} & set(kept) == set()


# ---------------------------------------------------------------- the one reader of a size ----


def test_a_size_is_read_by_one_reader_into_its_capital_form(home) -> None:
    assert overrides.capital("memory", "16g") == overrides.capital("memory", "16G") == "16G"
    assert overrides.capital("memory", "4096") == "4096" and overrides.capital("disk", "1t") == "1T"
    assert (overrides.mebibytes("16g"), overrides.mebibytes("512M"), overrides.mebibytes("512")) == (16384, 512, 512)
    assert create.Size.read("4", "8g", "64g") == create.Size.read(4, "8G", "64G") == create.Size(4, "8G", "64G")
    assert create.Size.read().given is False and create.Size.read(None, None, "40G").given is True
    # A refusal says the pattern's words, never the value typed.
    for kind, typed in (("memory", "16 GB"), ("disk", "0G"), ("disk", "-4G"), ("memory", "lots")):
        with pytest.raises(ValueError) as refused:
            overrides.capital(kind, typed)
        assert typed not in str(refused.value)
    for cores in ("0", "x", True, 1000):
        with pytest.raises(ValueError):
            create.Size.read(cores)
    # A size setting is read by the same reader: given as 40g, it is 40G.
    pinned("lab")
    given = overrides.run_values(("vm_disk_size=40g",), secrets=(), environ=os.environ)
    assert overrides.resolve(paths.home(REPO), "lab", given)["vm_disk_size"].value == "40G"


# ---------------------------------------------------------------- the stages not built yet ----


@pytest.mark.parametrize(
    ("values", "card", "shown"),
    [
        ({"names": ["delta"]}, "G10", "aw deploy --only provision"),
        ({"only": "ssh-config"}, "G8", "aw deploy --only ssh-config"),
        ({"check": True}, "G20", "aw deploy --check"),
        ({"only": "provision", "adopt": True}, "J7", "deploy's --adopt"),
        ({"only": "provision", "take_over": True}, "L25", "deploy's --take-over"),
        # A part of the VM's second layer (card G4): --only configure's, and --tags beside names, which is the same.
        ({"only": "configure", "tags": "claude_code"}, "G9", "--tags claude_code"),
        ({"names": ["delta"], "tags": "common,tailscale"}, "G9", "--tags tailscale"),
    ],
)
def test_each_stage_and_option_not_built_yet_is_refused_naming_its_card(values, card, shown) -> None:
    sentence = deploy.not_yet(values)
    assert sentence is not None and sentence.startswith("error: ") and f"card {card}" in sentence, sentence
    assert shown in sentence and "delta" not in sentence and "AAAA" not in sentence
    assert deploy.not_yet({"only": "provision", "names": ["delta"]}) is None
    # --only template is card E13's branch (tests/unit/test_aw_engine_template.py).
    assert deploy.not_yet({"only": "template"}) is None
    # Built: configure (card G4), with its parts of layer 1 or none, and --tags beside names, which is configure.
    assert deploy.not_yet({"only": "configure"}) is None
    assert deploy.not_yet({"only": "configure", "tags": "common,dev_tools"}) is None
    assert deploy.not_yet({"names": ["delta"], "tags": "vscode_server"}) is None


def test_deploy_is_built_by_this_card_and_its_handler_is_the_engine_s() -> None:
    assert VERBS["deploy"].handler == "engine.deploy:deploy"
    assert (EXITS["refused"], EXITS["failed"]) == (1, 2)


# ---------------------------------------------------------------- a lock another run holds ----


def test_a_lock_another_run_holds_is_refused_by_main_in_its_own_sentence(monkeypatch, capsys) -> None:
    from claude_vm.runs import locks

    sentence = "error: delta is held by job 0123456789abcdef (aw deploy delta), since 10:00: aw cancel 0123456789abcdef"

    def holding(*args, **kwargs):
        raise locks.Held(sentence, "machine", "0123456789abcdef")

    monkeypatch.setattr(dispatch, "dispatch", holding)
    assert main([str(REPO / "tools" / "aw"), "deploy", "delta", "--only", "provision"]) == EXITS["refused"] == 1
    said = capsys.readouterr()
    assert (said.out, said.err) == ("", sentence + "\n")

    # Any other error is raised as it is.
    def failing(*args, **kwargs):
        raise RuntimeError("not a lock")

    monkeypatch.setattr(dispatch, "dispatch", failing)
    with pytest.raises(RuntimeError):
        main([str(REPO / "tools" / "aw"), "deploy", "delta", "--only", "provision"])


# ---------------------------------------------------------------- what a repr may hold ----


def test_the_route_and_the_host_keep_the_run_s_values_and_environment_out_of_their_repr() -> None:
    hidden = {field.name for field in dataclasses.fields(routes.Route) if not field.repr}
    assert {"values", "environ"} <= hidden
    held = {field.name for field in dataclasses.fields(opened.Host) if not field.repr}
    assert {"values", "tokens", "password"} <= held


# ---------------------------------------------------------------- against the fake ----


def _mac(vmid: int) -> str:
    """The MAC the fake gives a VM it makes at vmid (tests/molecule/fake_pve_api.py's mac_for)."""
    return f"BC:24:11:{vmid // 65536 % 256:02X}:{vmid // 256 % 256:02X}:{vmid % 256:02X}"


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@dataclasses.dataclass
class Made:
    """The fake at base, its state in directory, the fleet's home, and the stand-in machines under root, at which each
    VM a run creates is reached."""

    base: str
    directory: Path
    home: Path
    root: Path
    machines: list[Machine] = dataclasses.field(default_factory=list)

    def stand_in(self, name: str, vmid: int) -> Machine:
        """A stand-in machine that the VM the next clone makes, at vmid, is: its SMBIOS UUID queued for that clone and
        its guest agent reporting the machine's address on the card the fake gives vmid. Clones take the queue in the
        order of their VMIDs, which the runs ask for under one section."""
        made_machine = machine(self.root, name)
        _pve(self.base, "PUT", "fake/clone-uuid", uuid=made_machine.uuid)
        result = json.dumps(interfaces(_mac(vmid), made_machine.address))
        _pve(self.base, "PUT", "fake/agent-answer", vmid=vmid, result=result)
        self.machines.append(made_machine)
        ssh_stand_in(self.root, self.machines)
        return made_machine

    def deploy(self, *words: str) -> subprocess.CompletedProcess[str]:
        """aw deploy <words> --only provision, as a person runs it, the stand-in ssh first on its PATH."""
        folder = self.root / SSH_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return run_aw("deploy", *words, "--only", "provision", home=self.home, first_on_path=(folder,), timeout=180)

    def config(self, vmid: int) -> dict:
        return _pve(self.base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]

    def record(self, vmid: int) -> Record:
        return Record.of(block.parse(self.config(vmid).get("description", "")) or {})

    def status(self, vmid: int) -> str:
        return _pve(self.base, "GET", f"nodes/pve/qemu/{vmid}/status/current")["data"]["status"]

    def sent(self) -> list[dict]:
        """Every request the engine's client sent the fake, in order."""
        return [entry for entry in _pve_requests(self.directory) if entry["agent"] == pin.USER_AGENT]

    def writes(self, since: int = 0) -> list[dict]:
        return [entry for entry in self.sent()[since:] if entry["method"] != "GET"]

    def filed(self) -> list[str]:
        """The lines of the product's known-hosts file in the fleet's home."""
        path = self.home / ".ssh" / "agent-warehouse.known_hosts"
        return path.read_text().splitlines() if path.exists() else []

    def listed(self) -> dict[int, dict]:
        """The cluster's listing of guests, by VMID, as the fake's own token reads it."""
        return {int(entry["vmid"]): entry for entry in _pve(self.base, "GET", "cluster/resources", type="vm")["data"]}

    def members(self) -> set[int]:
        return {vmid for vmid, entry in self.listed().items() if entry.get("pool") == ENGINE_POOL}


def _built(base: str) -> None:
    """The fake's template given this engine's record, as its build writes it, and made a member of the pool: the
    template this version builds, which a VM is cloned from."""
    settings = {name: found.value for name, found in overrides.resolve(paths.home(REPO), "pve").items()}
    wanted = template.wanted(settings)
    config = _pve(base, "GET", f"nodes/pve/qemu/{TEMPLATE}/config")["data"]
    made = Record(
        kind="template",
        uuid=proxmox.smbios_uuid(config),
        build=template.BUILD,
        image=DIGEST,
        image_url=str(wanted["image"]),
        packages=tuple(str(wanted["packages"]).split(",")),
    )
    notes = block.render(config.get("description", ""), made.fields())
    _pve(base, "PUT", f"nodes/pve/qemu/{TEMPLATE}/config", description=notes)
    _pve(base, "PUT", "pools", poolid=ENGINE_POOL, vms=str(TEMPLATE))


def _making(home: Path, tmp_path: Path, pki: dict, every_vm: bool) -> Iterator[Made]:
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        _pve(base, "PUT", "fake/agent-ready-after", polls=0)
        whole_fleet(home, base, directory, pki)
        if every_vm:
            engine_grant(base, "/vms", "VM.Audit")
        else:
            _pve(base, "PUT", "fake/pool-scope", token=ENGINE_TOKEN, pool=ENGINE_POOL)
        engine_grant(base, f"/storage/{STORAGE}", "Datastore.AllocateSpace,Datastore.Audit")
        _built(base)
        (home / ".ssh").mkdir(exist_ok=True)
        (home / ".ssh" / "id_ed25519.pub").write_text(KEY + "\n")
        yield Made(base, directory, home, tmp_path / "machines")


@pytest.fixture
def made(home, tmp_path, pki):
    """A whole fleet against the fake, the engine's token held to its table: VM.Audit on /vms and the storage's
    privileges granted as init grants them, the template this version builds in the pool, this computer's key in its
    home, and the guests' agents answering at once."""
    yield from _making(home, tmp_path, pki, True)


@pytest.fixture
def pooled(home, tmp_path, pki):
    """The same fleet under proxmox_audit_scope: pool: the token granted nothing on /vms, so that it sees the pool's
    members alone, its listing too."""
    yield from _making(home, tmp_path, pki, False)


def _ok(ran: subprocess.CompletedProcess[str], status: int = 0) -> None:
    assert ran.returncode == status, f"exit {ran.returncode}\n--- stdout\n{ran.stdout}\n--- stderr\n{ran.stderr}"


def test_two_vms_are_made_in_one_run_and_a_second_run_sends_nothing_but_gets(made) -> None:
    first, second = made.stand_in("one", FIRST), made.stand_in("two", SECOND)
    _ok(made.deploy("delta", "echo"))
    names = {}
    for vmid, stand_in in ((FIRST, first), (SECOND, second)):
        config, found = made.config(vmid), made.record(vmid)
        names[found.name] = vmid
        # Its record: the run's, its identity's UUID, provisioned, no size of its own, no list of its own, and no
        # agent's mark, which only an agent's run writes (card I).
        assert (found.kind, found.uuid, found.stage, found.provisioned) == ("vm", stand_in.uuid, "unfinished", True)
        assert (found.cores, found.memory, found.disk, found.repositories, found.made_by_agent) == (
            None,
            "",
            "",
            None,
            False,
        )
        assert len(found.job) == 16
        # Its label, and today's settings: the cloud-init user, this computer's key, DHCP, the start at boot, the
        # agent, the settings' cores and memory; the disk grown to vm_disk_size; no password, which the vault lacks.
        assert "agent-warehouse" in proxmox.proxmox_tags(config.get("tags", ""))
        assert (config["ciuser"], urllib.parse.unquote(config["sshkeys"]).strip()) == ("dev", KEY)
        assert (config["ipconfig0"], str(config["onboot"]), config["agent"]) == ("ip=dhcp", "1", "enabled=1")
        assert (int(config["cores"]), int(config["memory"]), int(config["balloon"])) == (2, 4096, 4096)
        assert "size=32G" in config["scsi0"] and "cipassword" not in config and "ciupgrade" not in config
        assert made.status(vmid) == "running"
        # Its key taken at first use and filed under its identity.
        assert stand_in.line in made.filed()
    assert sorted(names) == ["delta", "echo"] and made.members() >= {FIRST, SECOND}
    # Each a full clone of the template onto the fleet's storage, into the pool, with the record's first form.
    clones = [entry for entry in made.sent() if entry["path"].endswith(f"/qemu/{TEMPLATE}/clone")]
    assert sorted(int(entry["params"]["newid"]) for entry in clones) == [FIRST, SECOND]
    for entry in clones:
        params = entry["params"]
        assert (str(params["full"]), params["storage"], params["pool"]) == ("1", STORAGE, ENGINE_POOL)
        fields = block.parse(params["description"]) or {}
        assert (fields.get("kind"), fields.get("stage"), fields.get("uuid")) == ("vm", "cloning", None)
    sent = len(made.sent())
    filed = made.filed()
    _ok(made.deploy("delta", "echo"))
    assert made.writes(sent) == [] and len(made.sent()) > sent
    assert made.filed() == filed


def test_a_vm_no_key_and_no_password_would_reach_is_refused_before_anything_is_sent(made) -> None:
    (made.home / ".ssh" / "id_ed25519.pub").unlink()
    sent = len(made.sent())
    ran = made.deploy("delta")
    _ok(ran, 1)
    assert "The VM would be unreachable: set vm_ssh_public_keys" in ran.stderr
    assert made.writes(sent) == []


def test_a_template_with_no_cloud_init_drive_is_never_cloned(made) -> None:
    # A template converted by hand from a half-built VM, its cloud-init drive gone: its clones would boot without
    # their user and their keys (review 63, E12's L4).
    _pve(made.base, "PUT", f"nodes/pve/qemu/{TEMPLATE}/config", delete="ide2")
    sent = len(made.sent())
    ran = made.deploy("delta")
    _ok(ran, EXITS["refused"])
    assert f"({TEMPLATE}) has no cloud-init drive, so a VM cloned from it would boot without its user" in ran.stderr
    assert made.writes(sent) == []


def test_a_bare_deploy_is_refused_naming_the_card_that_builds_it_and_sends_nothing(made) -> None:
    sent = len(made.sent())
    ran = run_aw("deploy", "delta", home=made.home)
    _ok(ran, 1)
    assert "card G10" in ran.stderr and "--only provision" in ran.stderr and len(made.sent()) == sent


def test_a_vmid_another_guest_took_is_asked_for_again_and_that_guest_left_as_it_was(made) -> None:
    alpha, container = made.config(ALPHA), made.listed()[CONTAINER]
    for taken in (ALPHA, CONTAINER):
        _pve(made.base, "PUT", "fake/nextid-taken", vmid=taken)
    stand_in = made.stand_in("one", FIRST)
    _ok(made.deploy("delta"))
    clones = [entry for entry in made.sent() if entry["path"].endswith(f"/qemu/{TEMPLATE}/clone")]
    assert [int(entry["params"]["newid"]) for entry in clones] == [ALPHA, CONTAINER, FIRST]
    assert made.config(ALPHA) == alpha
    assert made.listed()[CONTAINER] == container
    assert made.record(FIRST).name == "delta" and made.record(FIRST).provisioned
    assert stand_in.line in made.filed()


@contextlib.asynccontextmanager
async def _open(*_: object, **__: object) -> AsyncIterator[None]:
    """A short section or a shield that holds nothing: a run of _clone in this process, with no job of its own."""
    yield


@pytest.mark.parametrize("fixture", ["made", "pooled"])
def test_a_clone_whose_answer_was_lost_is_sent_once_under_either_scope(request, monkeypatch, fixture) -> None:
    # The clone sent and its answer lost (the client's Unknown) while its task runs on. A token that may read every VM
    # reads its VMID until the clone lands there in this run's first form, and fails the VM with the way out; one that
    # may read the pool alone, which a clone joins only at its task's end, fails it at once. Never a second clone
    # (review 66's findings 1 and 2).
    fake = request.getfixturevalue(fixture)
    monkeypatch.setattr(create, "PAUSE", 0.05)
    monkeypatch.setattr(create, "LISTING_LAG", 1.0)
    monkeypatch.setattr(locks, "section", _open)
    sent = Proxmox.clone

    def lost(self: Proxmox, *args: object, **kwargs: object) -> None:
        sent(self, *args, **kwargs)
        raise Unknown("no answer came")

    monkeypatch.setattr(Proxmox, "clone", lost)
    _pve(fake.base, "PUT", "fake/task-polls", polls=10**6)
    # Under the node's scope the clone lands while its VMID is read; under the pool's it lands once the VM has failed.
    ending = threading.Timer(2.0, _pve, (fake.base, "PUT", "fake/task-end"))
    where = paths.home(REPO)
    try:
        with fleet.connect(where, hosts.reach(hosts.Layout(where), Request())) as host:
            values = {name: value.value for name, value in host.values.items()}
            source = template.compare(host.client, values, pool=host.pool, host_flag=host.host_flag)
            ctx = SimpleNamespace(
                job=SimpleNamespace(id=JOB),
                cancelled=threading.Event(),
                stopping=threading.Event(),
                emit=lambda *_, **__: None,
                shield=_open,
            )
            making = create.Making(ctx, host, None, create.Plan(), source, "delta", "stand-in")
            if fixture == "made":
                ending.start()
            with pytest.raises(create.Failed) as failed:
                asyncio.run(making._clone())
    finally:
        if ending.is_alive():
            ending.join()
        _pve(fake.base, "PUT", "fake/task-end")
    clones = [entry for entry in fake.sent() if entry["path"].endswith(f"/qemu/{TEMPLATE}/clone")]
    assert [int(entry["params"]["newid"]) for entry in clones] == [FIRST]
    if fixture == "made":
        assert failed.value.sentence == (
            f"delta's clone landed at VMID {FIRST}, but Proxmox's answer to it was lost, so this run has no task to"
            " own it by: delete that VM in Proxmox, then run this again"
        )
    else:
        assert failed.value.sentence.startswith(f"delta: Proxmox's answer to its clone at VMID {FIRST} was lost")
        assert f"may not read a VM outside the pool {ENGINE_POOL}" in failed.value.sentence
    # The clone landed, in this run's first form, which no later run may own.
    assert fake.record(FIRST).stage == "cloning" and fake.record(FIRST).job == JOB


def test_a_start_that_failed_is_resumed_by_the_next_run(made) -> None:
    stand_in = made.stand_in("one", FIRST)
    _pve(made.base, "PUT", "fake/task-fail", error="start failed: the node is out of memory", type="qmstart")
    ran = made.deploy("delta")
    _ok(ran, EXITS["failed"])
    assert "out of memory" in ran.stdout + ran.stderr
    # A member with its record and its settings, not provisioned, never started, its key not taken.
    found = made.record(FIRST)
    assert (found.name, found.stage, found.provisioned) == ("delta", "unfinished", False)
    assert made.config(FIRST)["ciuser"] == "dev" and made.status(FIRST) == "stopped"
    assert stand_in.line not in made.filed()
    _ok(made.deploy("delta"))
    assert made.record(FIRST).provisioned and made.status(FIRST) == "running"
    assert stand_in.line in made.filed()
    # Resumed, never cloned again.
    assert len([entry for entry in made.sent() if entry["path"].endswith("/clone")]) == 1


@pytest.mark.parametrize(("suffix", "method"), [("resize", "PUT"), ("status/start", "POST")])
def test_a_run_that_failed_at_a_step_is_resumed_from_there_by_the_next(made, suffix, method) -> None:
    stand_in = made.stand_in("one", FIRST)
    _pve(made.base, "PUT", "fake/refuse", suffix=suffix, methods=method)
    _ok(made.deploy("delta"), EXITS["failed"])
    found = made.record(FIRST)
    assert (found.stage, found.provisioned) == ("unfinished", False)
    assert made.config(FIRST)["ciuser"] == "dev" and stand_in.line not in made.filed()
    _ok(made.deploy("delta"))
    assert made.record(FIRST).provisioned and made.status(FIRST) == "running" and stand_in.line in made.filed()
    assert made.config(FIRST)["scsi0"].endswith("size=32G")
    assert len([entry for entry in made.sent() if entry["path"].endswith("/clone")]) == 1


def test_a_running_vm_resumed_before_it_was_provisioned_is_never_started_again(made) -> None:
    # A run that stopped after the start, at the address or the record write after it, leaves a running VM without
    # provisioned: the next run configures it again and starts nothing, which Proxmox would accept and then fail in
    # its task (review 66's finding 6).
    stand_in = made.stand_in("one", FIRST)
    _ok(made.deploy("delta"))
    found, config = made.record(FIRST), made.config(FIRST)
    unprovisioned = dataclasses.replace(found, provisioned=False)
    notes = block.render(config["description"], unprovisioned.fields())
    _pve(made.base, "PUT", f"nodes/pve/qemu/{FIRST}/config", description=notes)
    assert made.record(FIRST).provisioned is False and made.status(FIRST) == "running"
    sent = len(made.sent())
    _ok(made.deploy("delta"))
    writes = made.writes(sent)
    assert [entry for entry in writes if "ciuser" in entry["params"]]
    assert not [entry for entry in writes if entry["path"].endswith("/status/start")]
    assert made.record(FIRST).provisioned and stand_in.line in made.filed()


def test_a_clone_a_run_left_in_its_first_form_is_refused_to_every_later_run(made) -> None:
    made.stand_in("one", FIRST)
    # The record's write after the clone fails: the member keeps the first form, which only that run's job and its
    # clone's task own.
    _pve(made.base, "PUT", "fake/refuse", suffix=f"qemu/{FIRST}/config", methods="PUT")
    _ok(made.deploy("delta"), EXITS["failed"])
    assert (made.record(FIRST).stage, made.record(FIRST).uuid) == ("cloning", "")
    sent = len(made.sent())
    ran = made.deploy("delta")
    _ok(ran, EXITS["refused"])
    assert "delta" in ran.stderr and made.writes(sent) == []
    # Its run has ended, so nothing will write the rest: the refusal says to delete it (review 66's finding 3).
    assert f"Once that run has ended, nothing will: delete VMID {FIRST} in Proxmox, then run this again" in ran.stderr


def test_a_stop_during_the_clone_waits_for_its_end_and_its_record(made) -> None:
    # A clone cannot be undone: a Ctrl-C while its task runs lets it land and its record be written, so that the next
    # run resumes the VM, never left in its first form; nothing after the record is sent (review 66's finding 3).
    stand_in = made.stand_in("one", FIRST)
    _pve(made.base, "PUT", "fake/task-polls", polls=10**6)
    folder = made.root / SSH_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if "stopped" not in seen:
            if [entry for entry in made.sent() if entry["path"].endswith(f"/qemu/{TEMPLATE}/clone")]:
                os.kill(process.pid, signal.SIGINT)
                seen["stopped"] = time.monotonic()
        elif "alive" not in seen and time.monotonic() - float(seen["stopped"]) > 1.5:
            # Held by the clone's shield while its task runs; then the task ends.
            seen["alive"] = process.poll() is None
            _pve(made.base, "PUT", "fake/task-polls", polls=0)
            _pve(made.base, "PUT", "fake/task-end")

    rc, output = aw_at_a_terminal(
        "deploy", "delta", "--only", "provision", home=made.home, first_on_path=(folder,), act=act, timeout=180
    )
    said = output.decode(errors="replace")
    assert seen.get("alive") is True, said
    assert rc == EXITS["cancelled"] and "aw: stopping once the clone of delta is finished" in said, said
    found = made.record(FIRST)
    assert (found.stage, found.uuid, found.provisioned) == ("unfinished", stand_in.uuid, False)
    assert not [entry for entry in made.writes() if "ciuser" in entry["params"]]
    _ok(made.deploy("delta"))
    assert made.record(FIRST).provisioned and stand_in.line in made.filed()
    assert len([entry for entry in made.sent() if entry["path"].endswith("/clone")]) == 1


def _last_job() -> dict:
    """The record of the newest job of the fleet's home."""
    [newest, *_] = jobs.listed(paths.jobs_dir(paths.home(REPO)))
    return newest.read()


def _stopped_at(
    made: Made,
    reached,
    after=None,
    words: tuple[str, ...] = ("--only", "provision"),
    signum: int = signal.SIGINT,
):
    """aw deploy delta <words> at a terminal, sent a Ctrl-C (SIGINT, as the terminal sends its group one), or signum,
    once reached() says its phase is reached; after(output, process), where given, is asked at each read once it was
    sent. Its exit status, what it said, and how many requests the fake had had when the signal was sent."""
    folder = made.root / SSH_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}

    def act(chunk: bytes, output: bytes, master: int, process: subprocess.Popen) -> None:
        if "sent" not in seen and reached():
            seen["sent"] = len(made.sent())
            os.kill(process.pid, signum)
        elif "sent" in seen and after is not None:
            after(output, process)

    rc, output = aw_at_a_terminal(
        "deploy", "delta", *words, home=made.home, first_on_path=(folder,), act=act, poll=0.05, timeout=180
    )
    said = output.decode(errors="replace")
    assert "sent" in seen, said
    return rc, said, seen["sent"]


def _ends() -> list[tuple[str, str]]:
    """Each machine's end in the newest job's record: its outcome and the stage it reached."""
    return [(end["outcome"], end["stage"]) for end in (_last_job().get("machine_outcomes") or {}).values()]


def test_a_stop_during_the_agent_s_wait_ends_the_vm_stopped_in_the_job_s_record(made) -> None:
    # The first checkpoint's finding 1: the agent's wait ends on a stop by raising asyncio.CancelledError, which passed
    # the VM's run by, so the job's record held no end for it.
    stand_in = made.stand_in("one", FIRST)
    _pve(made.base, "PUT", "fake/agent-ready-after", polls=10**6)

    def polled() -> bool:
        return any(entry["path"].endswith("/agent/network-get-interfaces") for entry in made.sent())

    rc, said, _ = _stopped_at(made, polled)
    assert rc == EXITS["cancelled"] and _last_job()["outcome"] == "cancelled", said
    assert _ends() == [("cancelled", "provision")], _last_job()
    # The VM's own end, never the ladder's for a machine left without one (runs/attached.py's UNENDED).
    assert [end["why"] for end in _last_job()["machine_outcomes"].values()] == ["stopped"], _last_job()
    assert made.record(FIRST).stage == "unfinished" and not made.record(FIRST).provisioned
    _pve(made.base, "PUT", "fake/agent-ready-after", polls=0)
    _ok(made.deploy("delta"))
    assert made.record(FIRST).provisioned and stand_in.line in made.filed()


def test_a_stop_while_the_settings_call_s_answer_is_outstanding_sends_nothing_after_it(made) -> None:
    # The first checkpoint's finding 2: the settings call's answer held by the fake, a Ctrl-C meanwhile, the answer let
    # through once the run says it stops. Nothing is written after it, the resize least of all; the next run resumes
    # the VM from its settings.
    stand_in = made.stand_in("one", FIRST)
    state = made.directory / "pve"
    _pve(made.base, "PUT", "fake/hold", suffix=f"qemu/{FIRST}/config", param="ciuser")

    def released(output: bytes, _process: subprocess.Popen) -> None:
        if b"aw: stopping" in output and not (state / "release").exists():
            (state / "release").touch()

    rc, said, at = _stopped_at(made, (state / "held").exists, released)
    assert rc == EXITS["cancelled"], said
    [settings] = [entry for entry in made.writes() if "ciuser" in entry["params"]]
    assert made.sent().index(settings) < at, said
    assert made.writes(at) == [], made.writes(at)
    assert _ends() == [("cancelled", "provision")], _last_job()
    assert not made.record(FIRST).provisioned
    sent = len(made.sent())
    _ok(made.deploy("delta"))
    resent = [entry["path"].rsplit("/", 1)[-1] for entry in made.writes(sent)]
    assert "resize" in resent and made.record(FIRST).provisioned and stand_in.line in made.filed(), resent


def _holding(state: Path, let_through: str, hold: str):
    """reached for _stopped_at: the request held first, of method let_through, answered at once; true once the one held
    after it, of method hold, is held."""

    let: list[str] = []

    def reached() -> bool:
        held = state / "held"
        now = held.read_text() if held.is_file() else ""
        if now.startswith(f"{let_through} ") and not let:
            # Once: a release made again after the fake took the first would answer the next hold at once.
            let.append(now)
            (state / "release").touch()
        return now.startswith(f"{hold} ")

    return reached


def _released(state: Path):
    """after for _stopped_at: the answer held let through once the run says it stops."""

    let: list[bool] = []

    def after(output: bytes, _process: subprocess.Popen) -> None:
        if b"aw: stopping" in output and not let:
            let.append(True)
            (state / "release").touch()

    return after


@pytest.mark.parametrize(
    ("before", "suffix", "write"),
    [
        ("the resize's read", f"qemu/{FIRST}/config", "resize"),
        ("the read before the start", f"qemu/{FIRST}/status/current", "status/start"),
    ],
    ids=["resize", "start"],
)
def test_a_stop_during_a_write_s_own_read_sends_nothing_after_it(made, before, suffix, write) -> None:
    # Review 83's finding 1: the resize and the start each read the VM first, and a Ctrl-C during that read was followed
    # by the write. The settings call let through, then the read held by the fake while the run is stopped: the read's
    # answer let through once the run says it stops, nothing is written after it.
    made.stand_in("one", FIRST)
    state = made.directory / "pve"
    # The settings call's held and let through at once, so that the read held is the first after it of its path: the
    # resize's own, or, the resize sent and followed, the read of whether the VM runs, then the start's own read.
    _pve(made.base, "PUT", "fake/hold", suffix=f"qemu/{FIRST}/config", param="ciuser")
    _pve(made.base, "PUT", "fake/hold", suffix=suffix, methods="GET")
    rc, said, at = _stopped_at(made, _holding(state, "PUT", "GET"), _released(state))
    assert rc == EXITS["cancelled"], said
    [settings] = [entry for entry in made.writes() if "ciuser" in entry["params"]]
    assert made.sent().index(settings) < at, said
    resized = [entry for entry in made.writes() if entry["path"].endswith("/resize")]
    assert (write == "status/start") == bool(resized and made.sent().index(resized[0]) < at), (before, resized)
    assert made.writes(at) == [], (before, made.writes(at))
    assert not [entry for entry in made.writes() if entry["path"].endswith(f"/{write}")], before
    assert _ends() == [("cancelled", "provision")], _last_job()
    assert not made.record(FIRST).provisioned


@pytest.mark.parametrize("end", ["a second Ctrl-C", "a SIGTERM"])
def test_an_end_at_once_at_the_agent_s_wait_still_ends_the_vm_stopped(made, end) -> None:
    # Review 83's finding 2: a second Ctrl-C right after the run says it stops, or a SIGTERM, raises the ladder's
    # Abandoned wherever the loop's thread is, so the VM's own handler may never run: the ladder's end ends it.
    made.stand_in("one", FIRST)
    _pve(made.base, "PUT", "fake/agent-ready-after", polls=10**6)
    seconds: list[int] = []

    def polled() -> bool:
        return any(entry["path"].endswith("/agent/network-get-interfaces") for entry in made.sent())

    def again(output: bytes, process: subprocess.Popen) -> None:
        if b"aw: stopping" in output and not seconds:
            seconds.append(process.pid)
            os.kill(process.pid, signal.SIGINT)

    if end == "a SIGTERM":
        rc, said, _ = _stopped_at(made, polled, signum=signal.SIGTERM)
        assert rc == -signal.SIGTERM, said
        assert (_last_job()["outcome"], _last_job()["why"]) == ("cancelled", "SIGTERM"), said
    else:
        rc, said, _ = _stopped_at(made, polled, again)
        assert seconds and rc == EXITS["cancelled"], said
        assert (_last_job()["outcome"], _last_job()["why"]) == ("cancelled", "Ctrl-C twice"), said
    assert _ends() == [("cancelled", "provision")], _last_job()
    assert not made.record(FIRST).provisioned


def test_a_stopped_vm_is_left_stopped_unless_start_is_given(made) -> None:
    made.stand_in("one", FIRST)
    _ok(made.deploy("delta"))
    _pve(made.base, "POST", f"nodes/pve/qemu/{FIRST}/status/stop")
    assert made.status(FIRST) == "stopped"
    sent = len(made.sent())
    ran = made.deploy("delta")
    _ok(ran)
    assert made.status(FIRST) == "stopped" and made.writes(sent) == []
    assert "already exists and is stopped" in ran.stdout + ran.stderr
    sent = len(made.sent())
    _ok(made.deploy("delta", "--start"))
    assert made.status(FIRST) == "running"
    assert [entry["path"].rsplit("/", 2)[-2:] for entry in made.writes(sent)] == [["status", "start"]]


def test_a_size_is_given_at_the_create_refused_once_the_vm_exists_and_kept_on_a_repair(made) -> None:
    made.stand_in("one", FIRST)
    _ok(made.deploy("delta", "--cores", "4", "--memory", "8g", "--disk", "64g", "--repos", "one,two"))
    found, config = made.record(FIRST), made.config(FIRST)
    # Recorded in capital form, whatever was typed.
    assert (found.cores, found.memory, found.disk, found.repositories) == (4, "8G", "64G", ("one", "two"))
    assert (int(config["cores"]), int(config["memory"]), int(config["balloon"])) == (4, 8192, 8192)
    assert "size=64G" in config["scsi0"]
    # Another size for the VM that exists: refused, that VM failed, nothing written.
    sent = len(made.sent())
    ran = made.deploy("delta", "--cores", "8")
    _ok(ran, EXITS["failed"])
    assert "delta exists, with 4 cores, 8192 MiB of memory and a 64 GiB disk" in ran.stdout + ran.stderr
    assert made.writes(sent) == []
    # The size it has is no other.
    _ok(made.deploy("delta", "--cores", "4", "--memory", "8G"))
    # --repos adds to its own list.
    _ok(made.deploy("delta", "--repos", "three,one"))
    assert made.record(FIRST).repositories == ("one", "two", "three")
    # A repair sends no part of its size it is not given, and never resizes.
    sent = len(made.sent())
    _ok(made.deploy("delta", "--set", "proxmox_vm_force_update=true"))
    settings = [entry for entry in made.writes(sent) if "ciuser" in entry["params"]]
    assert len(settings) == 1 and {"cores", "memory", "balloon"} & set(settings[0]["params"]) == set()
    assert not [entry for entry in made.writes(sent) if entry["path"].endswith("/resize")]
    config = made.config(FIRST)
    assert (int(config["cores"]), int(config["memory"]), "size=64G" in config["scsi0"]) == (4, 8192, True)
    assert made.record(FIRST).cores == 4 and made.record(FIRST).memory == "8G"


def test_a_repair_never_resizes_and_refuses_a_disk_larger_than_the_vm_s(made) -> None:
    made.stand_in("one", FIRST)
    _ok(made.deploy("delta"))
    assert made.config(FIRST)["scsi0"].endswith("size=32G")
    sent = len(made.sent())
    _ok(made.deploy("delta", "--set", "vm_disk_size=40G", "--set", "proxmox_vm_force_update=true"))
    assert [entry for entry in made.writes(sent) if "ciuser" in entry["params"]]
    assert not [entry for entry in made.writes(sent) if entry["path"].endswith("/resize")]
    assert made.config(FIRST)["scsi0"].endswith("size=32G")
    # --disk larger than its disk, on a repair: refused as another size for a VM that exists is, nothing written, so
    # that its record never holds a disk it does not have (review 66's finding 4). Its own disk is no other size.
    sent = len(made.sent())
    ran = made.deploy("delta", "--disk", "64g", "--set", "proxmox_vm_force_update=true")
    _ok(ran, EXITS["failed"])
    assert "delta exists, with 2 cores, 4096 MiB of memory and a 32 GiB disk" in ran.stdout + ran.stderr
    assert made.writes(sent) == [] and made.record(FIRST).disk == ""
    _ok(made.deploy("delta", "--disk", "32g", "--set", "proxmox_vm_force_update=true"))
    assert made.record(FIRST).disk == "32G" and made.config(FIRST)["scsi0"].endswith("size=32G")


def test_repos_are_refused_for_a_vm_that_follows_the_setting(made) -> None:
    made.stand_in("one", FIRST)
    _ok(made.deploy("delta"))
    sent = len(made.sent())
    ran = made.deploy("delta", "--repos", "one")
    _ok(ran, EXITS["failed"])
    assert "delta exists and follows the github_projects setting" in ran.stdout + ran.stderr
    assert made.writes(sent) == [] and made.record(FIRST).repositories is None


def test_the_console_password_is_the_vault_s_and_never_in_the_host_s_repr(made) -> None:
    where = paths.home(REPO)
    reached = hosts.reach(hosts.Layout(where), Request())
    with fleet.connect(where, reached) as host:
        assert host.password is None
    values = overrides.resolve(where, "pve")
    key = vaultkey.open(
        where,
        key_file=values["vault_key_file"].value,
        key_command=values["vault_key_command"].value,
        environ=os.environ,
    )
    vault = dict(sealed.read(where, key) or {})
    vault[secrets.entry("vm_password")] = Secret(PASSWORD)
    with fleet.connect(where, reached, vault=vault) as host:
        assert host.password is not None and host.password.reveal() == PASSWORD
        assert PASSWORD not in repr(host)


def test_a_vm_with_a_console_password_and_no_key_is_made(made) -> None:
    where = paths.home(REPO)
    values = overrides.resolve(where, "pve")
    key = vaultkey.open(
        where,
        key_file=values["vault_key_file"].value,
        key_command=values["vault_key_command"].value,
        environ=os.environ,
    )
    before = dict(sealed.read(where, key) or {})
    sealed.write(where, key, {**before, secrets.entry("vm_password"): Secret(PASSWORD)}, expect=before)
    (made.home / ".ssh" / "id_ed25519.pub").unlink()
    stand_in = made.stand_in("one", FIRST)
    ran = made.deploy("delta")
    _ok(ran)
    config = made.config(FIRST)
    assert "cipassword" in config and "sshkeys" not in config and made.record(FIRST).provisioned
    assert stand_in.line in made.filed() and PASSWORD not in ran.stdout + ran.stderr
