"""The base template compared with its settings in plain code: lib/claude_vm/engine/template.py, which reads, and
lib/claude_vm/pve/template.py, which judges what was read (card E11, docs/architecture/engine.md §16.4 and §16.5).

What a build makes is wanted(settings), today's role's proxmox_template_wanted as a function: the same fields, the
role's own defaults for the options no row of the settings table names, held equal to its files here, and BUILD
beside it. compare reads the template through the API with the engine's token and says what it found, by the judgement
status calls too (card G16): given what status's own reads give, with the record's fields as pve/record.py's plain
parse gives them, it says what compare says, a template's record that names no UUID and a copy of another's refused
among them, and it imports nothing of the engine and not the client.

Against the fake Proxmox (tests/molecule/fake_pve_api.py), the engine's token held to its table of privileges, with
VM.Audit on /vms as init grants it under proxmox_audit_scope: node, or without it, under pool: each outcome; the fake's
own template, with this engine's record, is as this version builds it (tests/unit/test_filters.py's case on the fake's
template, carried); each difference is said with what it has and what is wanted, and the image's digest is never
compared; under proxmox_template_rebuild: false a difference is said with the deploy --only template line, and the
template is cloned from as it is, as today. Each refusal of the role's scenario
(roles/proxmox_template/molecule/default/side_effect.yml) that a comparison makes: an ID another node holds, an ID
that holds a VM a build never finished, a finished template of another name, a template with no record of the build;
and the engine's own beside them: a container, a guest outside the pool, a record of another kind, one that cannot be
read, a copy of another VM's, and a template's record that names no UUID, which only a VM's first form may (a person's
template with such a block pasted into its notes, and the record a build would write before it knew the template's
UUID, which it cannot make). Each leaves the template as it was. The scenario's other refusals - a custom image with no
checksum, an image of another family, a template a linked clone uses - are the build's (card E12).
Under pool an ID outside the pool reads unseen, never missing. A template of the person's own, which
proxmox_template_custom names, is only read and held to what a clone needs.

The invariant with a test of its own: compare sends nothing but GETs.

build builds the base template over root SSH (card E12) on a stand-in Proxmox host of tests/unit/aw_machine.py's,
reached as root through the stand-in ssh: its qm, virt-customize and virt-cat the role's scenario's fakes, its pvesh
reading qm's state as the API would give it, its dpkg-query and apt-get the test's, its curl, sha256sum and coreutils
the system's (on macOS, GNU's mv -T and stat --format stand-ins of the test's); the image served by
tests/molecule/fake_downloads.py over HTTP for the host's curl, and its checksum list and its signature, by a key of the
test's, by another over TLS for this computer. The first build runs today's steps in their order, as the role's scenario
(roles/proxmox_template/molecule/default/verify.yml) holds them, and records the image file and the template in
host.yml's made; a second run reads and changes nothing; a list that does not verify, or names no image, fetches nothing
on the host; an image whose digest differs is removed and both digests said; each command is pve/commands.py's, nothing
on its standard input, under the host's template section, with no ciupgrade; only the image tool's packages this run
installed are recorded, one held is there, and one half configured or unread stops the build; a link at the download's
name, the copy's or the image's is never written through, and an image directory another account could write in stops
the build; a difference is rebuilt at the same ID, and kept under proxmox_template_rebuild: false; a template a linked
clone uses is said with the way on; a template with no record of the build, a guest outside the pool and a template that
changed after it was judged - out of the pool, its record stripped, renamed, another guest - are never removed; an image
outside the Debian family, a custom image with no checksum of its own and a template of the person's own build nothing;
and every value a command takes is held to its form.

ensure is deploy's template stage (card E13), and `aw deploy --only template` reaches it through G3's handler, against
a whole fleet folder and the fake, standard input /dev/null and a refusing ssh first on PATH. The invariants with tests
of their own: a run with no terminal that finds the template missing, or differing under proxmox_template_rebuild:
true, and root SSH refusing, refuses before anything is created - the report's sentence, exit 1, the fake sent no write,
root SSH asked once, in batch mode, to run nothing, the sentence the run's NEXT STEPS line, the fleet's lock held
exclusively; and no run clones from a template that differs, since ensure returns only a usable template or raises. The
same template, and a difference kept under rebuild false, are said and left, root SSH never asked; this engine's
template with no cloud-init drive is never called the same; a cut build's VM at the ID is handed on as the run's line,
never built over; any other --only value still refused by G3's sentence. In process, on the stand-in host: with root SSH
answering, a run with no terminal builds, after one connection that runs nothing; at a terminal with root SSH refused,
the sentence says what would give a standing way, and nothing is read or run on the host.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from aw_kit import imports_of, run_aw
from aw_machine import SYSTEM_PATH, Machine, filed, machine, ssh_stand_in, target
from fleet_fixture import HOST, STORAGE, pinned, refusing_ssh, whole_fleet, with_port
from harness import (
    ENGINE_POOL,
    FAKES,
    _executable,
    _pve,
    _pve_requests,
    _start,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from signing import Signer

from claude_vm import proxmox
from claude_vm.engine import deploy, template
from claude_vm.engine.record import Record
from claude_vm.fleet import hosts, overrides, paths
from claude_vm.pve import commands, pin
from claude_vm.pve import record as block
from claude_vm.pve import template as judging
from claude_vm.pve.client import Proxmox, Token
from claude_vm.runs import job as jobs
from claude_vm.runs import locks
from claude_vm.schema.words import EXITS, Line
from claude_vm.secret import Secret
from claude_vm.ssh import runner

REPO = Path(__file__).resolve().parents[2]
# The module's certificate authority, which signs the fake's certificate, and the image vendor's key of the build's
# tests: cheap enough to make again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "signer"}
# The fake's template (tests/molecule/fake_pve_api.py's VMS), as the settings' defaults name it.
TEMPLATE = 9000
NAME = "ubuntu-24.04-cloudinit"
IMAGE = "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img"
DIGEST = "3f9a0c1e7b2d4a58" * 4
# An ID no guest of the fake has.
FREE = 9400
# Another image, Ubuntu's server image of the same release.
SERVER = IMAGE.replace("/minimal/", "/server/")


def _settings(**changes: object) -> dict[str, object]:
    """Every row's value for a run against the host lab, as fleet/overrides.py resolves them in this test's own home
    (the settings' defaults: proxmox_template_vmid 9000, proxmox_node pve and the rest), with changes over them: the
    host's settings this file's fleet is read with, the route to its VMs pinned direct first, as every fleet a test
    makes pins it (fleet_fixture.pinned)."""
    pinned("lab")
    resolved = overrides.resolve(paths.home(REPO), "lab")
    return {**{name: found.value for name, found in resolved.items()}, **changes}


def _role(role: str, part: str) -> dict:
    return yaml.safe_load((REPO / "roles" / role / part / "main.yml").read_text())


# --------------------------------------------------------------------------------- what a build makes ----


def test_wanted_has_today_s_fields_and_is_the_role_s_under_its_defaults(home) -> None:
    defaults, variables = _role("proxmox_template", "defaults"), _role("proxmox_template", "vars")
    assert tuple(variables["proxmox_template_wanted"]) == proxmox.TEMPLATE_FIELDS
    made = template.wanted(_settings())
    assert tuple(made) == proxmox.TEMPLATE_FIELDS
    assert made == {
        "cpu": defaults["proxmox_template_cpu_type"],
        "ostype": defaults["proxmox_template_ostype"],
        "bridge": defaults["proxmox_template_bridge"],
        "storage": defaults["proxmox_template_storage"],
        "scsihw": variables["proxmox_template_scsihw"],
        "agent": "1",
        "serial0": variables["proxmox_template_serial0"],
        "vga": variables["proxmox_template_vga"],
        "build": str(template.BUILD),
        "image": defaults["proxmox_template_image_url"],
        "packages": ",".join(defaults["proxmox_template_customize_packages"]),
    }


def test_the_options_no_row_names_are_the_roles_own_defaults_and_wired_as_today() -> None:
    variables = _role("proxmox_template", "vars")
    for name, value in template.OPTIONS.items():
        role = "proxmox_vm" if name == "proxmox_vm_disk" else "proxmox_template"
        given = _role(role, "defaults")[name]
        assert (tuple(given) if isinstance(given, list) else given) == value, name
    assert (judging.SCSIHW, judging.SERIAL0, judging.VGA) == (
        variables["proxmox_template_scsihw"],
        variables["proxmox_template_serial0"],
        variables["proxmox_template_vga"],
    )
    # The template's bridge and storage are the rows' unless given, as today's inventory wires them.
    wiring = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    for option, row in judging.FROM_ROWS.items():
        assert wiring[option] == "{{ " + row + " }}"
    assert set(template.OPTIONS) & set(judging.FROM_ROWS) == set()


def test_wanted_reads_the_rows_and_a_role_s_option_where_the_settings_give_one(home) -> None:
    assert template.wanted(_settings(proxmox_bridge="vmbr7", proxmox_storage="tank"))["bridge"] == "vmbr7"
    assert template.wanted(_settings(proxmox_storage="tank"))["storage"] == "tank"
    given = template.wanted(
        _settings(
            proxmox_bridge="vmbr7",
            proxmox_template_bridge="vmbr9",
            proxmox_template_cpu_type="x86-64-v3",
            proxmox_template_image_url=SERVER,
            proxmox_template_customize_packages=["qemu-guest-agent", "vim"],
        )
    )
    assert (given["bridge"], given["cpu"], given["packages"]) == ("vmbr9", "x86-64-v3", "qemu-guest-agent,vim")
    assert given["image"] == SERVER
    # No packages without the guest agent, which is what puts them there.
    assert template.wanted(_settings(proxmox_template_install_guest_agent=False))["packages"] == ""
    with pytest.raises(TypeError):
        template.wanted(_settings(proxmox_template_customize_packages="qemu-guest-agent"))


def test_the_build_is_a_whole_number_a_template_s_record_holds() -> None:
    assert isinstance(template.BUILD, int) and not isinstance(template.BUILD, bool) and template.BUILD >= 1
    made = Record(kind="template", uuid="9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c3", build=template.BUILD)
    assert Record.of(block.parse(block.render("", made.fields()))).build == template.BUILD


# ------------------------------------------------------------------------------ what a person reads ----


def _compared(outcome: str, **more: object) -> template.Compared:
    return template.Compared(outcome, NAME, TEMPLATE, "pve", ENGINE_POOL, **more)


def test_each_outcome_is_said_in_one_sentence() -> None:
    line = Line("deploy", {"only": "template"}, host_flag="")
    assert "is not on the host" in _compared(template.MISSING).said()
    assert "as its settings would build it" in _compared(template.SAME).said()
    differs = _compared(template.DIFFERENT, differences=("bridge vmbr1 → vmbr0", "cpu kvm64 → host"), line=line)
    assert differs.said() == (
        'The base template "ubuntu-24.04-cloudinit" (9000) differs from its settings: bridge vmbr1 → vmbr0; cpu kvm64 '
        "→ host."
    )
    kept = dataclasses.replace(differs, rebuild=False)
    assert kept.said().endswith(
        "It is kept as it is, since proxmox_template_rebuild is false: with it true, aw deploy --only template "
        "rebuilds it."
    )
    other_host = dataclasses.replace(kept, line=Line("deploy", {"only": "template"}, host_flag="lab2"))
    assert "aw deploy --only template --host lab2 rebuilds it" in other_host.said()
    # What Proxmox or a record gave is never read as a field of the sentence.
    braces = dataclasses.replace(kept, differences=("image https://x.invalid/{line}.img → https://y.invalid/a.img",))
    assert "{line}.img" in braces.said()
    assert "(proxmox_audit_scope: pool)" in _compared(template.UNSEEN).said()
    # A person's own template the token may not read is granted it by init, on its own path (card E1): never moved into
    # the pool, whose role would reach it.
    granting = "grants the token VM.Audit and VM.Clone on that template alone, which stays out of the pool."
    unseen = _compared(template.UNSEEN, custom=True, line=Line("init", {}, host_flag=""))
    assert unseen.said().endswith(f"(proxmox_audit_scope: pool): aw init {granting}")
    assert "in the pool, where" not in unseen.said()
    elsewhere = dataclasses.replace(unseen, line=Line("init", {}, host_flag="lab2"))
    assert elsewhere.said().endswith(f"aw init --host lab2 {granting}")
    for line in (None, Line("deploy", {"only": "template"}, host_flag="")):
        with pytest.raises(ValueError, match="said with the init line"):
            _compared(template.UNSEEN, custom=True, line=line)
    assert _compared(template.REFUSED, why=("Why.",)).said() == "Why."
    assert "only read and cloned" in _compared(template.CUSTOM, custom=True).said()
    lacking = _compared(template.CUSTOM, custom=True, why=("it has no net0", "its guest agent is off"))
    assert "cannot be cloned: it has no net0; its guest agent is off" in lacking.said()
    with pytest.raises(ValueError):
        _compared("rebuilt")


def test_only_the_same_template_a_kept_difference_or_a_person_s_lacking_nothing_is_cloned_and_refusals_told() -> None:
    usable = {outcome for outcome in template.OUTCOMES if _compared(outcome).usable}
    assert usable == {template.SAME, template.CUSTOM}
    assert not _compared(template.CUSTOM, why=("it has no net0",)).usable
    # A difference kept under proxmox_template_rebuild: false is cloned from as it is, as today's deploy does.
    assert _compared(template.DIFFERENT, rebuild=False).usable
    assert not _compared(template.DIFFERENT, rebuild=False).refused
    refused = {outcome for outcome in template.OUTCOMES if _compared(outcome, why=("x",)).refused}
    assert refused == {template.REFUSED, template.CUSTOM}
    assert not any(_compared(outcome).refused for outcome in template.OUTCOMES if outcome != template.REFUSED)


# --------------------------------------------------------------------------- against the fake Proxmox ----


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

    def compare(self, settings: dict[str, object], host_flag: str = "") -> template.Compared:
        with Proxmox(self.base, pin.kept(self.pki["ca"]), self.token) as client:
            found = template.compare(client, settings, pool=ENGINE_POOL, host_flag=host_flag)
        assert self.methods() <= {"GET"}, "compare sent something but a GET"
        return found

    def judged(self, settings: dict[str, object]) -> template.Compared:
        """What status says (card G16), which may not import the engine: pve/template.py's judgement alone, given the
        listing and the template's configuration as read (status makes the same answers from its own client's reads,
        by pve/answers.py's parsers) and the record's fields as pve/record.py's plain parse gives them, as they are:
        never engine/record.py's read, and nothing checked of them first."""
        base = judging.Base.of(settings, pool=ENGINE_POOL, host_flag="")
        with Proxmox(self.base, pin.kept(self.pki["ca"]), self.token) as client:
            listing, config = client.resources("vm"), client.config("pve", base.template_id)
        fields = block.parse(config.description)
        return judging.judge(base, judging.at(listing, base.template_id), config, fields, judging.wanted(settings))

    def methods(self) -> set[str]:
        """The methods of every request the engine's client sent the fake."""
        return {entry["method"] for entry in self.sent()}

    def sent(self) -> list[dict]:
        return [entry for entry in _pve_requests(self.directory) if entry["agent"] == pin.USER_AGENT]

    def config(self, guest: int = TEMPLATE) -> dict:
        """A guest's configuration, as today's token reads it."""
        return _pve(self.base, "GET", f"nodes/pve/qemu/{guest}/config")["data"]

    def identity(self, guest: int = TEMPLATE) -> str:
        return proxmox.smbios_uuid(self.config(guest))

    def plant(self, guest: int = TEMPLATE, **config: object) -> None:
        """A guest's configuration changed as a person changes it in Proxmox's page."""
        _pve(self.base, "PUT", f"nodes/pve/qemu/{guest}/config", **config)

    def member(self, *guests: int) -> None:
        _pve(self.base, "PUT", "pools", poolid=ENGINE_POOL, vms=",".join(str(guest) for guest in guests))

    def built(self, guest: int = TEMPLATE, **changes: object) -> Record:
        """The template at guest given this engine's record, as its build writes it, beside the notes it had, and made a
        member of the pool: the record."""
        made = Record(
            kind="template",
            uuid=self.identity(guest),
            build=template.BUILD,
            image=DIGEST,
            image_url=IMAGE,
            packages=("qemu-guest-agent",),
        )
        made = dataclasses.replace(made, **changes)
        self.plant(guest, description=block.render(self.config(guest).get("description", ""), made.fields()))
        self.member(guest)
        return made

    def made_template(self, guest: int, name: str) -> None:
        """A template of the person's own at guest, a clone of the fake's template converted, in no pool."""
        _pve(self.base, "POST", f"nodes/pve/qemu/{TEMPLATE}/clone", newid=guest, name=name)
        _pve(self.base, "POST", f"nodes/pve/qemu/{guest}/template")


def _fleet(directory: Path, pki: dict, scope: str):
    """The fake with the engine's token held to its table, granted VM.Audit on /vms where scope is node."""
    certificate = (pki["cert"], pki["key"])
    for base in fake_fleet(directory, engine=True, certificate=certificate, enforce=True):
        if scope == "node":
            engine_grant(base, "/vms", "VM.Audit")
        token_id, secret = engine_token(directory)
        yield Fleet(base, directory, Token(token_id, Secret(secret)), pki)


@pytest.fixture
def fleet(tmp_path, pki):
    """The fake, the token reading every guest of the node: proxmox_audit_scope: node, the default."""
    yield from _fleet(tmp_path, pki, "node")


@pytest.fixture
def pooled(tmp_path, pki):
    """The fake, the token reading its pool's guests alone: proxmox_audit_scope: pool."""
    yield from _fleet(tmp_path, pki, "pool")


def test_the_fake_s_template_with_this_engine_s_record_is_as_this_version_builds_it(fleet, home) -> None:
    # Carried from tests/unit/test_filters.py: the fake's template is what a build makes by default, so a scenario's
    # deploy finds nothing to rebuild once its template carries the engine's record.
    fleet.built()
    found = fleet.compare(_settings())
    assert (found.outcome, found.differences, found.why) == (template.SAME, (), ())
    assert (found.name, found.template_id, found.node, found.pool) == (NAME, TEMPLATE, "pve", ENGINE_POOL)
    assert found.usable and not found.refused


@pytest.mark.parametrize(
    ("settings", "planted", "recorded", "said"),
    [
        ({"proxmox_bridge": "vmbr1"}, {}, {}, "bridge vmbr0 → vmbr1"),
        ({}, {"net0": "virtio=BC:24:11:00:23:28,bridge=vmbr1"}, {}, "bridge vmbr1 → vmbr0"),
        ({}, {"cpu": "x86-64-v2-AES"}, {}, "cpu x86-64-v2-AES → host"),
        ({}, {"scsi0": "nfs-vms:9000/base-9000-disk-0.qcow2,size=3584M"}, {}, "storage nfs-vms → local-lvm"),
        ({}, {"vga": "std"}, {}, "vga std → serial0"),
        ({}, {"agent": "0"}, {}, "agent 0 → 1"),
        ({}, {}, {"build": 2}, "build 2 → 1"),
        ({}, {}, {"build": None}, "build unset → 1"),
        ({}, {}, {"packages": ()}, "packages unset → qemu-guest-agent"),
        ({"proxmox_template_install_guest_agent": False}, {}, {}, "packages qemu-guest-agent → unset"),
        ({}, {}, {"image_url": SERVER}, f"image {SERVER} → {IMAGE}"),
    ],
)
def test_each_difference_is_said_with_what_it_has_and_what_is_wanted(
    fleet, home, settings: dict, planted: dict, recorded: dict, said: str
) -> None:
    if planted:
        fleet.plant(**planted)
    fleet.built(**recorded)
    found = fleet.compare(_settings(**settings))
    assert found.outcome == template.DIFFERENT and found.rebuild
    (difference,) = found.differences
    assert difference.startswith(said), difference
    assert difference in found.said() and not found.usable and not found.refused


def test_the_image_s_digest_is_kept_but_never_compared(fleet, home) -> None:
    fleet.built(image="0" * 64)
    assert fleet.compare(_settings()).outcome == template.SAME


def test_a_difference_under_rebuild_false_is_only_said_with_the_line_that_rebuilds_it(fleet, home) -> None:
    fleet.built()
    before = fleet.config()
    found = fleet.compare(_settings(proxmox_bridge="vmbr2", proxmox_template_rebuild=False), host_flag="lab2")
    assert (found.outcome, found.rebuild, found.differences) == (template.DIFFERENT, False, ("bridge vmbr0 → vmbr2",))
    assert found.line == Line("deploy", {"only": "template"}, host_flag="lab2")
    assert "kept as it is" in found.said() and "aw deploy --only template --host lab2 rebuilds it" in found.said()
    assert fleet.config() == before


def test_a_difference_kept_under_rebuild_false_is_cloned_from_as_it_is_as_today(fleet, home) -> None:
    # Today's deploy keeps such a template and clones every new VM from it; the setting's own words say it is kept.
    fleet.built()
    kept = fleet.compare(_settings(proxmox_bridge="vmbr2", proxmox_template_rebuild=False))
    assert (kept.outcome, kept.rebuild, kept.usable, kept.refused) == (template.DIFFERENT, False, True, False)
    rebuilt = fleet.compare(_settings(proxmox_bridge="vmbr2"))
    assert (rebuilt.outcome, rebuilt.rebuild) == (template.DIFFERENT, True)
    assert not rebuilt.usable and not rebuilt.refused


def test_missing_is_said_where_the_token_may_read_the_id(fleet, home) -> None:
    found = fleet.compare(_settings(proxmox_template_vmid=FREE))
    assert (found.outcome, found.template_id) == (template.MISSING, FREE)
    assert "no guest has its ID, 9400" in found.said()
    assert any(entry["path"].endswith("/access/permissions") for entry in fleet.sent())


def test_under_pool_an_id_outside_the_pool_is_unseen_never_missing(pooled, home) -> None:
    # Nothing at the ID, and the fake's template at its ID outside the pool: the token sees neither.
    assert pooled.compare(_settings(proxmox_template_vmid=FREE)).outcome == template.UNSEEN
    found = pooled.compare(_settings())
    assert (found.outcome, found.template_id) == (template.UNSEEN, TEMPLATE)
    assert "a build asks the host over root SSH first" in found.said()
    # Its own template, a member of the pool, the token reads as under node.
    pooled.built()
    assert pooled.compare(_settings()).outcome == template.SAME


def _refused(fleet: Fleet, settings: dict[str, object], *said: str, guest: int | None = TEMPLATE) -> template.Compared:
    """compare under settings, refused with a sentence that says each of said, and the guest left as it was where one
    is named: a guest the fake keeps under the node pve as a VM."""
    before = fleet.config(guest) if guest is not None else None
    found = fleet.compare(settings)
    assert found.outcome == template.REFUSED and found.refused and not found.usable, found
    (why,) = found.why
    for each in said:
        assert each in why, why
    assert found.said() == why
    if before is not None:
        assert fleet.config(guest) == before
    return found


def test_an_id_a_guest_outside_the_pool_holds_is_refused_and_left(fleet, home) -> None:
    # The fake's template as it is, in no pool: a template of someone's.
    _refused(fleet, _settings(), 'ID 9000 holds a template named "ubuntu-24.04-cloudinit"', "no member of the pool")
    # A guest on the node that is down, which the listing names without its name: side_effect.yml's ID another node
    # holds.
    _refused(fleet, _settings(proxmox_template_vmid=8000), "ID 8000 holds a guest", "aw-fleet", guest=None)


def test_an_id_a_container_holds_is_refused(fleet, home) -> None:
    _refused(fleet, _settings(proxmox_template_vmid=9003), "ID 9003 holds a container", guest=None)


def test_the_engine_s_template_on_another_node_is_refused_with_the_node_to_set(fleet, home) -> None:
    fleet.built()
    _pve(fleet.base, "PUT", "fake/vm-node", vmid=TEMPLATE, node="pve3")
    found = fleet.compare(_settings())
    assert found.outcome == template.REFUSED
    assert "is on node pve3, but proxmox_node is pve" in found.said() and "set proxmox_node to pve3" in found.said()


def test_an_id_that_holds_a_vm_a_build_never_finished_is_refused(fleet, home) -> None:
    # alpha, in the pool, is no template: as a build that died before qm template left it.
    fleet.member(9010)
    _refused(fleet, _settings(proxmox_template_vmid=9010), "ID 9010 holds a VM", "qm destroy 9010", guest=9010)


def test_a_finished_template_of_another_name_is_refused(fleet, home) -> None:
    fleet.built()
    _refused(
        fleet,
        _settings(proxmox_template_name="other-template"),
        'holds a template named "ubuntu-24.04-cloudinit", not the finished template "other-template"',
    )


@pytest.mark.parametrize("rebuild", [True, False])
def test_a_template_with_no_record_of_this_engine_s_build_is_refused_and_never_rebuilt(fleet, home, rebuild) -> None:
    # The fake's template, made a member, with today's record line alone: a person's text to this engine. Refused
    # whatever proxmox_template_rebuild says: a VM of this engine's never stands on a template it may not rebuild.
    fleet.member(TEMPLATE)
    _refused(
        fleet,
        _settings(proxmox_template_rebuild=rebuild),
        "keeps no record of this engine's build",
        "never rebuilt",
        "proxmox_template_custom",
    )


def test_a_record_of_another_kind_is_refused(fleet, home) -> None:
    fleet.built(kind="golden")
    _refused(fleet, _settings(), "holds the record of a golden of this engine's")


def test_a_copy_of_another_vm_s_record_is_refused(fleet, home) -> None:
    # As a clone made without a description of its own carries its source's: beta's UUID in the template's record.
    fleet.built(uuid=fleet.identity(9011))
    _refused(fleet, _settings(), "never rebuilt or cloned", "written for another VM")


def test_a_record_that_cannot_be_read_is_refused(fleet, home) -> None:
    fleet.member(TEMPLATE)
    notes = fleet.config()["description"]
    fleet.plant(description=f"{notes}\n\nagent-warehouse record (format 1)\nkind: template\ncolour: blue\n{block.END}")
    _refused(fleet, _settings(), "never rebuilt or cloned", "cannot be read")


# A template's record as a block written by hand, or by a build before it knew the template's UUID: no uuid, at the
# stage cloning, which only a VM's first form may be (engine/record.py's Record).
UUIDLESS = {
    "kind": "template",
    "stage": "cloning",
    "build": str(template.BUILD),
    "image": DIGEST,
    "image_url": IMAGE,
    "packages": ("qemu-guest-agent",),
}


def test_a_person_s_template_with_a_record_naming_no_uuid_pasted_into_its_notes_is_refused(fleet, home) -> None:
    # A template of the person's own, a clone of the fake's made a template, at the ID and the name the settings give,
    # in the pool, with the block pasted into its notes: never the engine's, so never rebuilt, which would destroy it.
    fleet.made_template(FREE, NAME)
    fleet.plant(FREE, description=block.render(fleet.config(FREE).get("description", ""), UUIDLESS))
    fleet.member(FREE)
    _refused(
        fleet,
        _settings(proxmox_template_vmid=FREE, proxmox_bridge="vmbr2"),
        f'The template "{NAME}" ({FREE}) is never rebuilt or cloned',
        f"VM {FREE}'s notes cannot be read",
        "a VM's first form",
        guest=FREE,
    )


def test_a_template_s_record_written_before_its_uuid_is_known_is_refused_and_no_build_can_make_one(fleet, home) -> None:
    # A build cannot make one: a template's record names its UUID from its first write (card E12).
    with pytest.raises(ValueError, match="a VM's first form"):
        Record(**{**UUIDLESS, "build": template.BUILD})
    # And the notes a build that wrote one anyway left - stopped between qm template and writing the record again with
    # the UUID - are refused, the template never rebuilt or cloned, and left as it is.
    fleet.plant(description=block.render(fleet.config().get("description", ""), UUIDLESS))
    fleet.member(TEMPLATE)
    _refused(fleet, _settings(proxmox_bridge="vmbr2"), "never rebuilt or cloned", "a VM's first form")


# ------------------------------------------------------------------------- a template of the person's own ----


def test_a_template_of_the_person_s_own_is_only_read_and_cloned(fleet, home) -> None:
    fleet.made_template(9500, "my-template")
    before = fleet.config(9500)
    found = fleet.compare(_settings(proxmox_template_custom="my-template"))
    assert (found.outcome, found.template_id, found.why, found.custom) == (template.CUSTOM, 9500, (), True)
    assert found.usable and "only read and cloned" in found.said()
    assert fleet.config(9500) == before


def test_a_template_of_the_person_s_own_is_held_to_what_a_clone_needs(fleet, home) -> None:
    fleet.made_template(9500, "my-template")
    fleet.plant(9500, ciuser="someone")
    found = fleet.compare(_settings(proxmox_template_custom="my-template"))
    assert found.outcome == template.CUSTOM and found.refused and not found.usable
    assert any("cloud-init user" in why for why in found.why)
    assert "cannot be cloned" in found.said()
    absent = fleet.compare(_settings(proxmox_template_custom="no-such-template"))
    assert absent.outcome == template.CUSTOM and absent.template_id is None
    assert absent.why == ('there is no template named "no-such-template" on Proxmox',)


def test_a_template_of_the_person_s_own_outside_the_pool_is_unseen_under_pool(pooled, home) -> None:
    pooled.made_template(9500, "my-template")
    found = pooled.compare(_settings(proxmox_template_custom="my-template"), host_flag="lab2")
    assert (found.outcome, found.custom, found.template_id) == (template.UNSEEN, True, None)
    assert found.line == Line("init", {}, host_flag="lab2")
    assert "aw init --host lab2 grants the token VM.Audit and VM.Clone on that template alone" in found.said()
    # What init then grants (card E1: the clone role on the template's own path, for the user and the token), with the
    # template left out of the pool: it is read, and cloned from.
    engine_grant(pooled.base, "/vms/9500", "VM.Audit,VM.Clone")
    granted = pooled.compare(_settings(proxmox_template_custom="my-template"))
    assert granted.usable and (granted.outcome, granted.template_id) == (template.CUSTOM, 9500)


# ------------------------------------------------------------------------ the judgement status calls too ----


@pytest.mark.parametrize(
    ("settings", "recorded"),
    [
        ({}, {}),
        ({"proxmox_bridge": "vmbr2"}, {}),
        ({"proxmox_bridge": "vmbr2", "proxmox_template_rebuild": False}, {}),
        ({}, {"image_url": SERVER, "build": 2}),
        ({}, {"kind": "golden"}),
        ({"proxmox_template_name": "other-template"}, {}),
    ],
)
def test_status_judges_by_the_same_function_and_says_what_compare_says(fleet, home, settings, recorded) -> None:
    fleet.built(**recorded)
    given = _settings(**settings)
    assert fleet.judged(given) == fleet.compare(given)


def test_through_the_plain_reader_a_template_s_record_naming_no_uuid_is_refused_as_compare_refuses_it(
    fleet, home
) -> None:
    # parse takes the block as a record; the judgement holds it to the template's own UUID itself, whichever reader
    # read it, so status says what deploy says, and E12's road over root SSH judges no such block a template to rebuild.
    fleet.plant(description=block.render(fleet.config().get("description", ""), UUIDLESS))
    fleet.member(TEMPLATE)
    assert block.parse(fleet.config()["description"])["kind"] == "template"
    judged = fleet.judged(_settings(proxmox_bridge="vmbr2"))
    assert (judged.outcome, judged.refused, judged.usable) == (template.REFUSED, True, False)
    assert "never rebuilt or cloned" in judged.said() and "a VM's first form" in judged.said()
    assert judged == fleet.compare(_settings(proxmox_bridge="vmbr2"))


def test_through_the_plain_reader_a_copy_of_another_vm_s_record_is_refused_as_compare_refuses_it(fleet, home) -> None:
    # beta's UUID in the template's record, as a clone made without a description of its own carries its source's:
    # parse takes it, copied would call it a copy, and the judgement asks copied itself.
    fleet.built(uuid=fleet.identity(9011))
    fields = block.parse(fleet.config()["description"])
    assert block.copied(fields, fleet.identity())
    judged = fleet.judged(_settings())
    assert (judged.outcome, judged.refused, judged.usable) == (template.REFUSED, True, False)
    assert "never rebuilt or cloned" in judged.said() and "written for another VM" in judged.said()
    assert judged == fleet.compare(_settings())


def test_the_judgement_imports_nothing_of_the_engine_nor_the_client(home) -> None:
    # So that status may import it (card G15's boundary keeps claude_vm.engine, pydantic, ansible and the MCP SDK out of
    # aw status, and status reads through a client of its own).
    module = "claude_vm.pve.template"
    added = imports_of(module=module, home=home) - imports_of(module="claude_vm", home=home)
    assert module in added, sorted(added)
    ours = sorted(name for name in added if name.split(".")[0] == "claude_vm")
    assert [name for name in ours if name == "claude_vm.engine" or name.startswith("claude_vm.engine.")] == [], ours
    assert "claude_vm.pve.client" not in ours, ours
    assert {name.split(".")[0] for name in added} & {"pydantic", "ansible", "mcp"} == set(), sorted(added)


# ---------------------------------------------------------------------------------------- the invariant ----


def test_compare_sends_nothing_but_gets(fleet, home) -> None:
    # Every outcome's road, one after another; the guests the fake holds are as they were.
    before = {guest: fleet.config(guest) for guest in (TEMPLATE, 9010, 9011)}
    seen = [fleet.compare(_settings()).outcome]
    fleet.built()
    after = fleet.config()
    seen += [
        fleet.compare(_settings()).outcome,
        fleet.compare(_settings(proxmox_bridge="vmbr2")).outcome,
        fleet.compare(_settings(proxmox_bridge="vmbr2", proxmox_template_rebuild=False)).outcome,
        fleet.compare(_settings(proxmox_template_vmid=FREE)).outcome,
        fleet.compare(_settings(proxmox_template_custom=NAME)).outcome,
    ]
    assert seen == [
        template.REFUSED,
        template.SAME,
        template.DIFFERENT,
        template.DIFFERENT,
        template.MISSING,
        template.CUSTOM,
    ]
    sent = fleet.sent()
    assert {entry["method"] for entry in sent} == {"GET"}
    asked = {entry["path"].rsplit("/api2/json", 1)[-1] for entry in sent}
    assert {"/cluster/resources", f"/nodes/pve/qemu/{TEMPLATE}/config", "/access/permissions"} <= asked
    assert fleet.config() == after and {guest: fleet.config(guest) for guest in (9010, 9011)} == {
        guest: before[guest] for guest in (9010, 9011)
    }


@pytest.mark.parametrize(("pool", "host_flag"), [("", ""), (None, ""), (ENGINE_POOL, None)])
def test_compare_is_given_the_pool_and_the_host_s_flag(fleet, home, pool, host_flag) -> None:
    with Proxmox(fleet.base, pin.kept(fleet.pki["ca"]), fleet.token) as client, pytest.raises((TypeError, ValueError)):
        template.compare(client, _settings(), pool=pool, host_flag=host_flag)
    assert fleet.sent() == []


# ---------------------------------------------------------------------------------- the build over root SSH ----

# The image the lab's mirror serves, and the name its URL and the checksum list give it.
IMAGE_FILE = "noble-minimal-cloudimg-amd64.img"
IMAGE_BYTES = b"a cloud image, as the test stands in for one\n"
IMAGE_SHA = hashlib.sha256(IMAGE_BYTES).hexdigest()
# Where the vendor's server keeps the checksum list and its signature, as Ubuntu lays them out.
LISTED_AT = "ubuntu/minimal/releases/noble/release"
FAKE_DOWNLOADS = FAKES / "fake_downloads.py"
# The host's programs a build runs, by name: nothing else, and no shell or interpreter given a script.
HOST_PROGRAMS = {
    "pvesh",
    "qm",
    "mkdir",
    "id",
    "stat",
    "sha256sum",
    "curl",
    "mv",
    "rm",
    "cp",
    "virt-cat",
    "virt-customize",
    "dpkg-query",
    "apt-get",
}

# The host's pvesh, as root reads with it: the cluster's listing and a guest's configuration, as JSON, from fake_qm.sh's
# state - each guest's configuration, its pool and its node (pve unless a file says another) - and any other guests a
# test lists in guests.json; a configuration with its digest and its notes decoded, as the API gives them. Every call
# is a JSON line of pvesh.log.
PVESH = r"""
import hashlib
import json
import os
import re
import sys
import urllib.parse

STATE = sys.argv[1]
ARGS = sys.argv[2:]
with open(os.path.join(STATE, "pvesh.log"), "a", encoding="utf-8") as log:
    log.write(json.dumps(ARGS) + "\n")


def kept(name, default=""):
    path = os.path.join(STATE, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def configuration(guest):
    with open(os.path.join(STATE, f"{guest}.conf"), "rb") as handle:
        data = handle.read()
    values = {}
    for line in data.decode("utf-8").splitlines():
        key, _, value = line.partition(": ")
        values[key] = urllib.parse.unquote(value) if key == "description" else value
    if "template" in values:
        values["template"] = int(values["template"])
    return values, hashlib.sha1(data).hexdigest()


if ARGS == ["get", "/cluster/resources", "--type", "vm", "--output-format", "json"]:
    listing = json.loads(kept("guests.json", "[]"))
    for name in sorted(os.listdir(STATE)):
        found = re.fullmatch(r"([0-9]+)[.]conf", name)
        if found is None:
            continue
        guest = int(found.group(1))
        values, _ = configuration(guest)
        entry = {
            "id": f"qemu/{guest}",
            "type": "qemu",
            "vmid": guest,
            "node": kept(f"{guest}.node", "pve"),
            "name": values.get("name", ""),
            "status": "stopped",
            "template": values.get("template", 0),
        }
        if kept(f"{guest}.pool"):
            entry["pool"] = kept(f"{guest}.pool")
        listing.append(entry)
    print(json.dumps(listing))
    sys.exit(0)
asked = re.fullmatch(r"/nodes/([a-z0-9-]+)/qemu/([0-9]+)/config", ARGS[1]) if len(ARGS) == 4 else None
if asked is not None and ARGS[0] == "get" and ARGS[2:] == ["--output-format", "json"]:
    if not os.path.exists(os.path.join(STATE, f"{asked.group(2)}.conf")):
        where = f"nodes/{asked.group(1)}/qemu-server/{asked.group(2)}.conf"
        sys.stderr.write(f"Configuration file '{where}' does not exist\n")
        sys.exit(2)
    values, digest = configuration(int(asked.group(2)))
    print(json.dumps({**values, "digest": digest}))
    sys.exit(0)
sys.stderr.write("stand-in pvesh: a read it does not answer\n")
sys.exit(2)
"""


# GNU's `mv -fT <from> <to>` and `stat --format=<format> <path>`, as the build runs them, where the system's are BSD's
# (macOS): the rename over whatever has the name, never into a directory, and what the name itself is, never through a
# link; anything else they are asked is refused.
GNU = r"""
import os, stat, sys

program, words = sys.argv[1], sys.argv[2:]
if program == "mv" and len(words) == 3 and words[0] == "-fT":
    try:
        os.replace(words[1], words[2])
    except OSError as error:
        sys.exit(f"mv: {error}")
elif program == "stat" and len(words) == 2 and words[0].startswith("--format="):
    try:
        found = os.lstat(words[1])
    except OSError as error:
        sys.exit(f"stat: {error}")
    kinds = {stat.S_IFDIR: "directory", stat.S_IFLNK: "symbolic link", stat.S_IFREG: "regular file"}
    kind = kinds.get(stat.S_IFMT(found.st_mode), "weird file")
    mode = format(stat.S_IMODE(found.st_mode), "o")
    print(words[0].removeprefix("--format=").replace("%u", str(found.st_uid)).replace("%a", mode).replace("%F", kind))
else:
    sys.exit(f"stand-in {program}: words it does not answer: {words}")
"""


def _dpkg_query(root: Path) -> str:
    """The host's dpkg-query for one package, as dpkg answers --show in the format asked, ${Status} as its three words
    and ${db:Status-Status} as the last: a package in root/installed is wanted and installed; one root/dpkg names, a
    line `<package> <want> <state>`, as that line says, where a state of `exit-<n>` ends the read with that status; any
    other none found."""
    return f"""#!/bin/sh
format=$2
for package in "$@"; do :; done
line=
if grep -qxF -e "$package" "{root / "installed"}"; then
  line="install installed"
elif [ -f "{root / "dpkg"}" ]; then
  line=$(awk -v p="$package" '$1 == p {{ print $2, $3 }}' "{root / "dpkg"}")
fi
if [ -z "$line" ]; then
  echo "dpkg-query: no packages found matching $package" >&2
  exit 1
fi
want=${{line% *}}
state=${{line#* }}
case "$state" in exit-*) echo "dpkg-query: the status database could not be read" >&2; exit "${{state#exit-}}" ;; esac
case "$format" in
  *'${{db:Status-Status}}'*) printf '%s\\n' "$state" ;;
  *'${{Status}}'*) printf '%s ok %s\\n' "$want" "$state" ;;
  *) echo "stand-in dpkg-query: a format it does not answer" >&2; exit 2 ;;
esac
"""


def _apt_get(root: Path) -> str:
    """The host's apt-get: each call a line of apt.log, and each package install names installed, as dpkg then says."""
    return f"""#!/bin/sh
printf '%s\\n' "$*" >> "{root / "apt.log"}"
if [ "$1" = install ]; then
  shift
  for package in "$@"; do
    case "$package" in -*) ;; *) printf '%s\\n' "$package" >> "{root / "installed"}" ;; esac
  done
fi
exit 0
"""


def _stand_ins(host: Machine, root: Path) -> None:
    """The Proxmox host's programs a build runs, in its bin/: qm, virt-customize and virt-cat the role's scenario's
    fakes (tests/molecule/), each given files of the test's own under root; pvesh, dpkg-query and apt-get the test's.
    curl, sha256sum, coreutils and env are the system's, sha256sum through shasum where the system has none."""
    qm = root / "qm"
    qm.mkdir(parents=True)
    (root / "installed").write_text("")
    (root / "pvesh.py").write_text(PVESH)
    tools = {
        "qm": f'#!/bin/sh\nFAKE_QM_STATE="{qm}" exec bash "{FAKES / "fake_qm.sh"}" "$@"\n',
        "virt-customize": (
            f'#!/bin/sh\nFAKE_VIRT_CUSTOMIZE_LOG="{root / "customize.log"}" '
            f'exec sh "{FAKES / "fake_virt_customize.sh"}" "$@"\n'
        ),
        "virt-cat": (
            f'#!/bin/sh\nFAKE_VIRT_CAT_OS_RELEASE="{root / "os-release"}" exec sh "{FAKES / "fake_virt_cat.sh"}" "$@"\n'
        ),
        "pvesh": f'#!/bin/sh\nexec "{sys.executable}" -I "{root / "pvesh.py"}" "{qm}" "$@"\n',
        "dpkg-query": _dpkg_query(root),
        "apt-get": _apt_get(root),
    }
    if shutil.which("sha256sum", path=os.pathsep.join(SYSTEM_PATH)) is None:
        tools["sha256sum"] = '#!/bin/sh\nexec shasum -a 256 "$@"\n'
    if sys.platform == "darwin":
        # BSD's mv has no -T and its stat no --format: GNU's, as a Proxmox host has them, for the build's own words.
        (root / "gnu.py").write_text(GNU)
        for name in ("mv", "stat"):
            tools[name] = f'#!/bin/sh\nexec "{sys.executable}" -I "{root / "gnu.py"}" {name} "$@"\n'
    # The machine's dpkg-query is a link to the stand-in every machine shares: taken out, never written through.
    (host.bin / "dpkg-query").unlink()
    for name, text in tools.items():
        _executable(host.bin / name, text)


@pytest.fixture(scope="module")
def signer() -> Signer:
    """The image's vendor's key, as the test stands in for it."""
    return Signer()


@dataclasses.dataclass
class Lab:
    """A Proxmox host, a stand-in machine of tests/unit/aw_machine.py's reached as root, whose programs are _stand_ins'
    over the files in root; the mirror the image is fetched from by the host's curl, over plain HTTP; the vendor's
    server, over TLS, that serves the checksum list and its signature by signer; the host lab's host.yml, which made
    is recorded in; and the run's job."""

    host: Machine
    root: Path
    mirror: str
    vendor: str
    signer: Signer
    ca: Path
    job: jobs.Job

    @property
    def images(self) -> Path:
        return self.root / "images"

    @property
    def qm(self) -> Path:
        return self.root / "qm"

    def serve(self, data: bytes = IMAGE_BYTES) -> None:
        where = self.root / "mirror" / "images" / IMAGE_FILE
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(data)

    def sums(self, listing: bytes | None = None, *, signed: bytes | None = None) -> None:
        """The checksum list, the image's line among others, and its signature of signed (the list unless given)."""
        body = listing
        if body is None:
            body = f"{'0' * 64} *{IMAGE_FILE}.manifest\n{IMAGE_SHA} *{IMAGE_FILE}\n".encode()
        folder = self.root / "vendor" / LISTED_AT
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SHA256SUMS").write_bytes(body)
        (folder / "SHA256SUMS.gpg").write_text(self.signer.sign(body if signed is None else signed))

    def settings(self, **changes: object) -> dict[str, object]:
        """The run's settings: the image the mirror serves, held to the vendor's list signed by the test's key, kept
        in images, with the vendor's CA trusted and no proxy; the image tool left as the host has it, unless changes
        say."""
        listed = f"{self.vendor}/{LISTED_AT}/SHA256SUMS"
        wired = {
            "proxmox_template_image_url": f"{self.mirror}/images/{IMAGE_FILE}",
            "proxmox_template_image_checksum": f"sha256:{listed}",
            "proxmox_template_image_signature": f"{listed}.gpg",
            "proxmox_template_image_key": self.signer.public(),
            "proxmox_template_image_fingerprint": self.signer.fingerprint,
            "proxmox_template_image_dir": str(self.images),
            "proxmox_template_manage_libguestfs": False,
            "tls_ca_file": str(self.ca),
            "network_proxy": "none",
        }
        return _settings(**{**wired, **changes})

    def build(self, settings: dict[str, object] | None = None) -> template.Compared:
        ctx = SimpleNamespace(job=self.job, emit=self.job.emit, cancelled=asyncio.Event())
        host = SimpleNamespace(
            name="lab",
            pool=ENGINE_POOL,
            host_flag="",
            layout=hosts.Layout(paths.home(REPO)),
            environ=dict(os.environ),
        )
        given = self.settings() if settings is None else settings
        return asyncio.run(template.build(ctx, host, given, target=target(self.host)))  # type: ignore[arg-type]

    def qm_calls(self) -> list[list[str]]:
        """Every qm call, its words as fake_qm.sh logs them, quoted as bash's %q quotes them."""
        log = self.qm / "calls.log"
        return [line.split() for line in log.read_text().splitlines()] if log.exists() else []

    def conf(self, guest: int = TEMPLATE) -> list[str]:
        path = self.qm / f"{guest}.conf"
        return path.read_text().splitlines() if path.exists() else []

    def identity(self, guest: int = TEMPLATE) -> str:
        return next(line.split("uuid=", 1)[1] for line in self.conf(guest) if line.startswith("smbios1: "))

    def record(self, guest: int = TEMPLATE) -> dict:
        notes = next(line.split(": ", 1)[1] for line in self.conf(guest) if line.startswith("description: "))
        return block.parse(urllib.parse.unquote(notes))

    def customised(self) -> list[str]:
        log = self.root / "customize.log"
        return log.read_text().splitlines() if log.exists() else []

    def apt(self) -> list[str]:
        log = self.root / "apt.log"
        return log.read_text().splitlines() if log.exists() else []

    def fetched(self) -> list[str]:
        """Every path the mirror was asked for: the host's curl's."""
        log = self.root / "mirror" / "requests"
        return log.read_text().splitlines() if log.exists() else []

    def asked(self) -> list[str]:
        """Every path the vendor's server was asked for: this computer's."""
        log = self.root / "vendor" / "requests"
        return log.read_text().splitlines() if log.exists() else []

    def reads(self) -> list[list[str]]:
        log = self.qm / "pvesh.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def made(self) -> list[tuple[str, str, str]]:
        found = hosts.Layout(paths.home(REPO)).fields("lab").made
        return [(one.what, one.id, one.by) for one in found]

    def in_images(self) -> list[str]:
        return sorted(os.listdir(self.images)) if self.images.exists() else []


@pytest.fixture
def lab(engine_guard, home, tmp_path, pki, signer):
    machines = tmp_path / "machines"
    host = machine(machines, "pve", account="root")
    filed(host)
    engine_guard.first_on_path(ssh_stand_in(machines, [host]))
    root = tmp_path / "lab"
    _stand_ins(host, root)
    for served in ("mirror", "vendor"):
        (root / served).mkdir()
    ca = root / "ca.pem"
    ca.write_text(pki["ca"])
    # host.yml of the host lab, which made is recorded in: its API is never asked anything here.
    with_port(9, "lab")
    servers = []
    job = jobs.create(paths.jobs_dir(paths.home(REPO)), "deploy", "deploy", "lab", [], {"kind": "terminal"}, True)
    try:
        mirror, mirror_port = _start(FAKE_DOWNLOADS, str(root / "mirror"))
        servers.append(mirror)
        vendor, vendor_port = _start(FAKE_DOWNLOADS, str(root / "vendor"), "--tls", str(pki["cert"]), str(pki["key"]))
        servers.append(vendor)
        made = Lab(host, root, f"http://127.0.0.1:{mirror_port}", f"https://127.0.0.1:{vendor_port}", signer, ca, job)
        made.serve()
        made.sums()
        yield made
    finally:
        jobs.drop(job)
        for server in servers:
            server.terminate()
            server.wait(timeout=10)


def _stopped(lab: Lab, settings: dict[str, object] | None = None) -> str:
    with pytest.raises(template.Refused) as refused:
        lab.build(settings)
    return str(refused.value)


def test_the_first_build_runs_today_s_steps_in_their_order_and_records_what_it_made(lab) -> None:
    built = lab.build()
    identity = lab.identity()
    assert (built.outcome, built.identity, built.differences) == (template.SAME, identity, ())
    # The role's scenario's verify: the calls, in their order; the settings the template has; what went into the image.
    calls = lab.qm_calls()
    assert [call[:3] for call in calls] == [
        ["create", str(TEMPLATE), "--name"],
        ["set", str(TEMPLATE), "--scsi0"],
        ["set", str(TEMPLATE), "--ide2"],
        ["template", str(TEMPLATE)],
    ]
    copy = lab.images / f"agent-warehouse-{TEMPLATE}.img"
    settings = lab.settings()
    storage, bridge = settings["proxmox_storage"], settings["proxmox_bridge"]
    conf = lab.conf()
    for line in (
        f"name: {NAME}",
        f"net0: virtio,bridge={bridge}",
        f"scsi0: {storage}:0,import-from={copy},discard=on",
        f"ide2: {storage}:cloudinit",
        "boot: order=scsi0",
        "ipconfig0: ip=dhcp",
        "agent: enabled=1",
        "cpu: host",
        "ostype: l26",
        "cores: 2",
        "memory: 2048",
        "template: 1",
    ):
        assert line in conf, (line, conf)
    assert (lab.qm / f"{TEMPLATE}.pool").read_text().strip() == ENGINE_POOL
    assert lab.customised() == [f"--add {copy} --install qemu-guest-agent --truncate /etc/machine-id"]
    # The record names the template's own UUID from its first write, the image's digest and what went into it.
    assert lab.record() == {
        "kind": "template",
        "uuid": identity,
        "build": str(template.BUILD),
        "image": IMAGE_SHA,
        "image_url": f"{lab.mirror}/images/{IMAGE_FILE}",
        "packages": ("qemu-guest-agent",),
    }
    # The list and its signature read here, the image fetched on the host; the copy and the download's name gone.
    assert lab.asked() == [f"/{LISTED_AT}/SHA256SUMS", f"/{LISTED_AT}/SHA256SUMS.gpg"]
    assert lab.fetched() == [f"/images/{IMAGE_FILE}"]
    assert lab.in_images() == [IMAGE_FILE] and (lab.images / IMAGE_FILE).read_bytes() == IMAGE_BYTES
    assert lab.made() == [("file", str(lab.images / IMAGE_FILE), "engine"), ("template", str(TEMPLATE), "engine")]
    # The image tool as the host has it: nothing asked of apt.
    assert lab.apt() == []


def test_a_second_run_reads_and_changes_nothing(lab) -> None:
    lab.build()
    before = (lab.qm_calls(), lab.conf(), lab.customised(), lab.fetched(), lab.asked(), lab.made(), lab.in_images())
    reads = len(lab.reads())
    again = lab.build()
    assert again.outcome == template.SAME
    assert (lab.qm_calls(), lab.conf(), lab.customised(), lab.fetched(), lab.asked(), lab.made(), lab.in_images()) == (
        before
    )
    # Two reads of root's, the listing and the template's configuration, and nothing else on the host.
    assert [read[1] for read in lab.reads()[reads:]] == ["/cluster/resources", f"/nodes/pve/qemu/{TEMPLATE}/config"]


@pytest.mark.parametrize(
    "listing",
    [
        pytest.param("tampered", id="a list whose signature does not verify"),
        pytest.param("unlisted", id="a list that names no image"),
    ],
)
def test_a_list_that_does_not_verify_or_names_no_image_fetches_nothing_on_the_host(lab, listing) -> None:
    good = f"{IMAGE_SHA} *{IMAGE_FILE}\n".encode()
    if listing == "tampered":
        lab.sums(good.replace(IMAGE_SHA[:8].encode(), b"0" * 8), signed=good)
    else:
        lab.sums(f"{IMAGE_SHA} *other.img\n".encode())
    refused = _stopped(lab)
    assert "The image's SHA-256 was not taken, so nothing was fetched on the host" in refused, refused
    assert lab.fetched() == [] and lab.qm_calls() == [] and not lab.images.exists() and lab.made() == []
    # Before anything on the host: the listing alone, which said nothing is at the ID.
    assert [read[1] for read in lab.reads()] == ["/cluster/resources"]


def test_an_image_whose_digest_differs_is_removed_and_both_digests_are_said(lab) -> None:
    other = IMAGE_BYTES.replace(b"cloud", b"cloud-other")
    lab.serve(other)
    refused = _stopped(lab)
    assert IMAGE_SHA in refused and hashlib.sha256(other).hexdigest() in refused, refused
    assert "it was removed, and the base template was not built" in refused
    assert lab.fetched() == [f"/images/{IMAGE_FILE}"]
    assert lab.in_images() == [] and lab.qm_calls() == [] and lab.customised() == [] and lab.made() == []


def test_each_command_is_the_commands_module_s_with_nothing_on_its_standard_input_under_the_section(
    lab, monkeypatch
) -> None:
    seen: list[tuple[list[str], object, list[str]]] = []
    real = runner.run

    async def recording(job, at, command, stdin, limits, env, *more, **named):
        seen.append((list(command), stdin, sorted(locks.locks_held().values())))
        return await real(job, at, command, stdin, limits, env, *more, **named)

    monkeypatch.setattr(runner, "run", recording)
    lab.build(lab.settings(proxmox_template_manage_libguestfs=True))
    url, image = f"{lab.mirror}/images/{IMAGE_FILE}", str(lab.images / IMAGE_FILE)
    copy = str(lab.images / f"agent-warehouse-{TEMPLATE}.img")
    commands_run = [command for command, _, _ in seen]
    programs = [next(word for word in command if word != "env" and "=" not in word) for command in commands_run]
    assert programs == [
        "pvesh",
        "mkdir",
        "id",
        "stat",
        "sha256sum",
        "rm",
        "curl",
        "sha256sum",
        "mv",
        "dpkg-query",
        "dpkg-query",
        "apt-get",
        "apt-get",
        "virt-cat",
        "rm",
        "cp",
        "virt-customize",
        "qm",
        "qm",
        "qm",
        "qm",
        "rm",
        "pvesh",
        "pvesh",
    ]
    assert set(programs) <= HOST_PROGRAMS
    # The directory made and read as it is; the fetch: the host's curl to a name of its own cleared first, the host's
    # sha256sum, the rename; each the module's own words.
    assert commands_run[1:9] == [
        commands.folder(str(lab.images)),
        commands.account(),
        commands.kind_of(str(lab.images)),
        commands.digest_of(image),
        commands.removed(f"{image}.part"),
        commands.fetch(url, f"{image}.part"),
        commands.digest_of(f"{image}.part"),
        commands.put_in_place(f"{image}.part", image),
    ]
    assert commands_run[13:17] == [
        commands.os_release(image),
        commands.removed(copy),
        commands.copied(image, copy),
        commands.customised(copy, ("qemu-guest-agent",)),
    ]
    assert commands_run[21] == commands.removed(copy)
    # Nothing on any command's standard input, and every one under the host's template section.
    assert {stdin for _, stdin, _ in seen} == {None}
    assert all(any("template-lab" in held for held in holding) for _, _, holding in seen), seen
    # No word ciupgrade: a clone's first boot upgrades by Proxmox's default.
    assert not any("ciupgrade" in word for command in commands_run for word in command)
    # The image tool, missing on the host, installed by this run and recorded.
    assert lab.apt() == ["update", "install --yes libguestfs-tools dhcpcd-base"]
    assert [entry for entry in lab.made() if entry[0] == "package"] == [
        ("package", "libguestfs-tools", "engine"),
        ("package", "dhcpcd-base", "engine"),
    ]


def test_only_the_image_tool_s_packages_this_run_installed_are_recorded(lab) -> None:
    (lab.root / "installed").write_text("libguestfs-tools\n")
    lab.build(lab.settings(proxmox_template_manage_libguestfs=True))
    assert lab.apt() == ["update", "install --yes dhcpcd-base"]
    assert [entry for entry in lab.made() if entry[0] == "package"] == [("package", "dhcpcd-base", "engine")]
    # Once both are there, nothing more is installed or recorded.
    lab.build(lab.settings(proxmox_template_manage_libguestfs=True, proxmox_template_cpu_type="x86-64-v3"))
    assert lab.apt() == ["update", "install --yes dhcpcd-base"]
    assert [entry for entry in lab.made() if entry[0] == "package"] == [("package", "dhcpcd-base", "engine")]


def _packages(lab: Lab) -> list[tuple[str, str, str]]:
    return [entry for entry in lab.made() if entry[0] == "package"]


def test_a_package_the_administrator_holds_is_there_and_never_installed_again_or_recorded(lab) -> None:
    # dpkg wants libguestfs-tools held, and it is installed: `hold ok installed`; dhcpcd-base it has never had.
    (lab.root / "dpkg").write_text("libguestfs-tools hold installed\n")
    built = lab.build(lab.settings(proxmox_template_manage_libguestfs=True))
    assert built.outcome == template.SAME
    assert lab.apt() == ["update", "install --yes dhcpcd-base"]
    assert _packages(lab) == [("package", "dhcpcd-base", "engine")]


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("unknown not-installed", id="not installed"),
        pytest.param("deinstall config-files", id="removed, its configuration kept"),
    ],
)
def test_a_package_dpkg_says_is_not_installed_is_installed_and_recorded(lab, line) -> None:
    (lab.root / "dpkg").write_text(f"libguestfs-tools {line}\n")
    (lab.root / "installed").write_text("dhcpcd-base\n")
    built = lab.build(lab.settings(proxmox_template_manage_libguestfs=True))
    assert built.outcome == template.SAME
    assert lab.apt() == ["update", "install --yes libguestfs-tools"]
    assert _packages(lab) == [("package", "libguestfs-tools", "engine")]


@pytest.mark.parametrize(
    ("line", "words"),
    [
        pytest.param(
            "install half-configured",
            'dpkg says libguestfs-tools is "half-configured" on the Proxmox host, neither installed nor absent',
            id="half configured",
        ),
        pytest.param(
            "install exit-2",
            "Whether libguestfs-tools is installed could not be read on the Proxmox host: it ended with status 2",
            id="a read that failed",
        ),
        pytest.param(
            "install exit-255",
            "Whether libguestfs-tools is installed could not be read on the Proxmox host",
            id="a read that ended 255",
        ),
    ],
)
def test_a_package_neither_installed_nor_absent_or_unread_is_refused_and_nothing_installed(lab, line, words) -> None:
    (lab.root / "dpkg").write_text(f"libguestfs-tools {line}\n")
    refused = _stopped(lab, lab.settings(proxmox_template_manage_libguestfs=True))
    assert words in refused and "the base template was not built" in refused, refused
    assert lab.apt() == [] and _packages(lab) == []
    assert lab.qm_calls() == [] and lab.customised() == [] and lab.in_images() == [IMAGE_FILE]


VICTIM = b"a file of root's outside the image directory\n"


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(f"{IMAGE_FILE}.part", id="at the download's name"),
        pytest.param(f"agent-warehouse-{TEMPLATE}.img", id="at the copy's name"),
    ],
)
def test_a_link_planted_at_a_name_the_build_writes_is_never_written_through(lab, name) -> None:
    victim = lab.root / "victim"
    victim.write_bytes(VICTIM)
    lab.images.mkdir(mode=0o755)
    (lab.images / name).symlink_to(victim)
    built = lab.build()
    assert built.outcome == template.SAME
    assert victim.read_bytes() == VICTIM
    image = lab.images / IMAGE_FILE
    assert not image.is_symlink() and image.read_bytes() == IMAGE_BYTES
    assert lab.in_images() == [IMAGE_FILE]
    assert lab.made() == [("file", str(image), "engine"), ("template", str(TEMPLATE), "engine")]


def test_a_link_at_the_image_s_name_to_a_directory_is_replaced_never_written_into(lab) -> None:
    elsewhere = lab.root / "elsewhere"
    elsewhere.mkdir()
    lab.images.mkdir(mode=0o755)
    (lab.images / IMAGE_FILE).symlink_to(elsewhere, target_is_directory=True)
    built = lab.build()
    assert built.outcome == template.SAME
    assert sorted(os.listdir(elsewhere)) == []
    image = lab.images / IMAGE_FILE
    assert not image.is_symlink() and image.read_bytes() == IMAGE_BYTES


def _a_link(lab: Lab) -> None:
    elsewhere = lab.root / "elsewhere"
    elsewhere.mkdir()
    lab.images.symlink_to(elsewhere, target_is_directory=True)


def _writable(lab: Lab) -> None:
    lab.images.mkdir()
    lab.images.chmod(0o775)


def _another_s(lab: Lab) -> None:
    # The account that builds is another than the one the directory belongs to: the host's id says another number.
    _executable(lab.host.bin / "id", f"#!/bin/sh\necho {os.getuid() + 1}\n")


@pytest.mark.parametrize(
    ("planted", "words"),
    [
        pytest.param(_a_link, "is a symbolic link, not a directory", id="a link"),
        pytest.param(_writable, "may be written by accounts other than root (mode 775)", id="writable by its group"),
        pytest.param(_another_s, f"belongs to the account numbered {os.getuid()}, not to root", id="another's"),
    ],
)
def test_an_image_directory_another_account_could_write_in_stops_the_build_before_anything_is_fetched(
    lab, planted, words
) -> None:
    planted(lab)
    refused = _stopped(lab)
    assert f"The image directory {lab.images} " in refused and words in refused, refused
    assert "The base template was not built." in refused
    assert lab.fetched() == [] and lab.qm_calls() == [] and lab.made() == []
    assert not (lab.root / "elsewhere").exists() or sorted(os.listdir(lab.root / "elsewhere")) == []


def test_a_difference_is_rebuilt_at_the_same_id_with_a_uuid_of_its_own(lab) -> None:
    lab.build()
    first = lab.identity()
    built = lab.build(lab.settings(proxmox_template_cpu_type="x86-64-v3"))
    assert built.outcome == template.SAME and built.identity == lab.identity() != first
    assert [call[0] for call in lab.qm_calls()] == ["create", "set", "set", "template", "destroy"] + [
        "create",
        "set",
        "set",
        "template",
    ]
    assert "cpu: x86-64-v3" in lab.conf() and lab.record()["uuid"] == lab.identity()
    # The image already held to its digest is not fetched again, and the template is recorded once.
    assert lab.fetched() == [f"/images/{IMAGE_FILE}"]
    assert lab.made() == [("file", str(lab.images / IMAGE_FILE), "engine"), ("template", str(TEMPLATE), "engine")]
    assert lab.in_images() == [IMAGE_FILE]


def test_a_difference_kept_under_rebuild_false_changes_nothing(lab) -> None:
    lab.build()
    calls = lab.qm_calls()
    kept = lab.build(lab.settings(proxmox_template_cpu_type="x86-64-v3", proxmox_template_rebuild=False))
    assert kept.outcome == template.DIFFERENT and kept.usable and lab.qm_calls() == calls


def test_a_template_a_linked_clone_uses_is_said_with_the_way_on_and_the_copy_removed(lab) -> None:
    lab.build()
    conf = lab.conf()
    (lab.qm / f"{TEMPLATE}.linked").write_text("")
    refused = _stopped(lab, lab.settings(proxmox_template_cpu_type="x86-64-v3"))
    assert f"can't remove VM {TEMPLATE}" in refused and "linked clone" in refused, refused
    assert "set proxmox_template_rebuild to false" in refused
    assert lab.conf() == conf and lab.qm_calls()[-1][:2] == ["destroy", str(TEMPLATE)]
    assert lab.in_images() == [IMAGE_FILE]


@pytest.mark.parametrize(
    ("planted", "words"),
    [
        pytest.param({"pool": ENGINE_POOL}, "keeps no record of this engine's build", id="no record of the build"),
        pytest.param({"pool": "someone-else"}, "is no member of the pool", id="a guest outside the pool"),
    ],
)
def test_a_template_with_no_record_or_a_guest_outside_the_pool_is_never_destroyed_or_created(
    lab, planted, words
) -> None:
    # Today's template, finished, as a person's or another program's: no record of this engine's in its notes.
    lab.qm.joinpath(f"{TEMPLATE}.conf").write_text(
        f"name: {NAME}\nsmbios1: uuid=9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c3\ntemplate: 1\n"
    )
    lab.qm.joinpath(f"{TEMPLATE}.pool").write_text(planted["pool"] + "\n")
    refused = _stopped(lab)
    assert words in refused, refused
    assert lab.qm_calls() == [] and lab.fetched() == [] and lab.asked() == [] and lab.made() == []
    assert lab.conf()[0] == f"name: {NAME}"


def _out_of_the_pool(lab: Lab) -> None:
    (lab.qm / f"{TEMPLATE}.pool").unlink()


def _in_another_pool(lab: Lab) -> None:
    (lab.qm / f"{TEMPLATE}.pool").write_text("someone-else\n")


def _record_stripped(lab: Lab) -> None:
    path = lab.qm / f"{TEMPLATE}.conf"
    path.write_text("".join(line for line in path.read_text().splitlines(True) if not line.startswith("description: ")))


def _renamed(lab: Lab) -> None:
    path = lab.qm / f"{TEMPLATE}.conf"
    path.write_text(path.read_text().replace(f"name: {NAME}\n", "name: renamed-by-hand\n"))


def _another_guest(lab: Lab) -> None:
    # Another guest at the ID with the template's record copied into its notes: its own SMBIOS UUID, the record's not.
    path = lab.qm / f"{TEMPLATE}.conf"
    old = f"smbios1: uuid={lab.identity()}\n"
    path.write_text(path.read_text().replace(old, "smbios1: uuid=0f0e0d0c-0b0a-4909-8807-060504030201\n"))


def _rebuilt_elsewhere(lab: Lab) -> None:
    # Rebuilt by another run, as differing as before: another UUID, in the configuration and the record alike.
    path = lab.qm / f"{TEMPLATE}.conf"
    path.write_text(path.read_text().replace(lab.identity(), "0f0e0d0c-0b0a-4909-8807-060504030201"))


@pytest.mark.parametrize(
    ("change", "words"),
    [
        pytest.param(_out_of_the_pool, "is no member of the pool", id="taken out of the pool"),
        pytest.param(_in_another_pool, "is no member of the pool", id="moved to another pool"),
        pytest.param(_record_stripped, "keeps no record of this engine's build", id="its record stripped"),
        pytest.param(_renamed, 'holds a template named "renamed-by-hand"', id="renamed"),
        pytest.param(_another_guest, "is never rebuilt or cloned", id="another guest, the record copied"),
        pytest.param(_rebuilt_elsewhere, "differs from its settings: cpu", id="rebuilt under another UUID"),
    ],
)
def test_a_template_that_changed_after_it_was_judged_is_never_destroyed(lab, monkeypatch, change, words) -> None:
    lab.build()
    real = template._fetched

    async def changed(*args, **named):
        await real(*args, **named)
        # Between the judgement and the removal, in the minutes the image takes: a person's change at the ID.
        change(lab)

    monkeypatch.setattr(template, "_fetched", changed)
    refused = _stopped(lab, lab.settings(proxmox_template_cpu_type="x86-64-v3"))
    assert f"The guest at ID {TEMPLATE} changed after it was judged to differ from its settings" in refused, refused
    assert words in refused, refused
    assert "destroy" not in [call[0] for call in lab.qm_calls()] and lab.conf()
    assert lab.made() == [("file", str(lab.images / IMAGE_FILE), "engine"), ("template", str(TEMPLATE), "engine")]
    assert lab.in_images() == [IMAGE_FILE]


def test_an_image_outside_the_debian_family_is_refused_by_name(lab) -> None:
    (lab.root / "os-release").write_text('NAME="Fedora Linux"\nID=fedora\n')
    refused = _stopped(lab)
    assert "is no Ubuntu or Debian image" in refused and "ID=fedora" in refused, refused
    assert lab.qm_calls() == [] and lab.customised() == [] and lab.in_images() == [IMAGE_FILE]


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        pytest.param(
            {"proxmox_template_image_checksum": judging.OPTIONS["proxmox_template_image_checksum"]},
            "with no proxmox_template_image_checksum of its own",
            id="a custom image with the default's checksum",
        ),
        pytest.param(
            {"proxmox_template_image_checksum": ""},
            "with no proxmox_template_image_checksum of its own",
            id="a custom image with no checksum",
        ),
        pytest.param(
            {"proxmox_template_image_checksum": f"md5:{'0' * 32}"},
            "proxmox_template_image_checksum is sha256:",
            id="a checksum of another kind",
        ),
        pytest.param(
            {"proxmox_template_image_checksum": f"sha256:{IMAGE_SHA}"},
            "so proxmox_template_image_checksum must be sha256:<the URL",
            id="a signature with no list to sign",
        ),
        pytest.param(
            {"proxmox_template_custom": "my-own-template"},
            "which is only read and cloned: no base template is built",
            id="a template of the person's own",
        ),
    ],
)
def test_what_cannot_be_built_is_refused_before_anything_is_read(lab, changes, words) -> None:
    refused = _stopped(lab, lab.settings(**changes))
    assert words in refused, refused
    assert lab.host.calls() == [] and lab.asked() == [] and lab.fetched() == []


def test_the_default_image_is_held_to_ubuntu_s_signed_list_beside_it(home) -> None:
    image = template._Image.of(_settings(), TEMPLATE)
    listed = str(template.OPTIONS["proxmox_template_image_checksum"]).removeprefix("sha256:")
    assert (image.url, image.checksum, image.signature) == (IMAGE, listed, f"{listed}.gpg")
    assert (image.directory, image.path) == (template.IMAGE_DIR, f"{template.IMAGE_DIR}/{IMAGE.rsplit('/', 1)[1]}")
    assert image.copy == f"{template.IMAGE_DIR}/agent-warehouse-{TEMPLATE}.img" and image.manage
    # A list of another server's is signed by none unless the settings name its signature, and "" turns Ubuntu's off.
    other = _settings(proxmox_template_image_url=SERVER, proxmox_template_image_checksum="sha256:https://example.org/S")
    assert template._Image.of(other, TEMPLATE).signature == ""
    assert template._Image.of(_settings(proxmox_template_image_signature=""), TEMPLATE).signature == ""
    # Without the guest agent, the download itself is imported and nothing is customised.
    plain = template._Image.of(_settings(proxmox_template_install_guest_agent=False), TEMPLATE)
    assert (plain.copy, plain.packages, plain.imported) == ("", (), plain.path)


@pytest.mark.parametrize(
    ("changes", "asked"),
    [
        pytest.param({"proxmox_template_image_signature": ""}, ["SHA256SUMS"], id="an unsigned list"),
        pytest.param(
            {"proxmox_template_image_signature": "", "proxmox_template_image_checksum": f"sha256:{IMAGE_SHA}"},
            [],
            id="the digest itself",
        ),
    ],
)
def test_an_unsigned_list_or_the_digest_itself_holds_the_image_alone(lab, changes, asked) -> None:
    built = lab.build(lab.settings(**changes))
    assert built.outcome == template.SAME and lab.record()["image"] == IMAGE_SHA
    assert lab.asked() == [f"/{LISTED_AT}/{name}" for name in asked]


CREATE = {
    "name": NAME,
    "ostype": "l26",
    "cpu": "host",
    "cores": 2,
    "memory": 2048,
    "bridge": "vmbr0",
    "uuid": "9a0e1c2d-3b4f-4a5b-8c6d-7e8f90a1b2c3",
    "notes": "agent-warehouse record (format 1)\nkind: template\nend of agent-warehouse record",
    "pool": ENGINE_POOL,
}


@pytest.mark.parametrize(
    ("made", "words"),
    [
        pytest.param(lambda: commands.create(TEMPLATE, **{**CREATE, "bridge": "vmbr0 --pool x"}), "bridge"),
        pytest.param(lambda: commands.create(TEMPLATE, **{**CREATE, "name": "-x"}), "name"),
        pytest.param(lambda: commands.create(TEMPLATE, **{**CREATE, "notes": "--delete 1"}), "notes"),
        pytest.param(lambda: commands.create(99, **CREATE), "guest's ID"),
        pytest.param(lambda: commands.import_disk(TEMPLATE, "local-lvm", "/var/../etc/img"), "absolute path"),
        pytest.param(lambda: commands.import_disk(TEMPLATE, "local-lvm", "images/x.img"), "absolute path"),
        pytest.param(lambda: commands.fetch("file:///etc/shadow", "/var/x.img"), "URL"),
        pytest.param(lambda: commands.fetch("https://a/x.img b", "/var/x.img"), "URL"),
        pytest.param(lambda: commands.removed("/var/lib/-rf"), "absolute path"),
        pytest.param(lambda: commands.install(["-y"]), "package"),
        pytest.param(lambda: commands.customised("/var/x.img", ["vim,bash"]), "package"),
        pytest.param(lambda: commands.config("pve/../x", TEMPLATE), "node"),
    ],
)
def test_each_value_a_command_takes_is_held_to_its_form(made, words) -> None:
    with pytest.raises(ValueError, match=words):
        made()


# ------------------------------------------------------------------------------------------- ensure (card E13) ----

# The run's lines, as run_aw gives them: what it says above its view and at its end.
DEPLOY_TEMPLATE = ("deploy", "--only", "template")


@dataclasses.dataclass
class Deploying:
    """A whole fleet against the fake (tests/unit/fleet_fixture.py), the engine's token held to its table with VM.Audit
    on /vms, its template the one this version builds, in the pool; and a refusing ssh first on a run's PATH, which
    answers no address and logs each call (tests/unit/aw_machine.py's unrouted.log)."""

    fleet: Fleet
    home: Path
    ssh: Path
    # The requests the fake had been sent, and the calls of the ssh, once the fleet was made: aw init's.
    since: int
    ssh_since: int

    def deploy(self, *words: str) -> subprocess.CompletedProcess[str]:
        """aw deploy <words>, standard input /dev/null: a run with no terminal, as an agent's or a script's."""
        return run_aw(*words, home=self.home, first_on_path=(self.ssh,), timeout=120)

    def sent(self) -> list[dict]:
        """Every request the engine's client sent the fake since the fleet was made."""
        return self.fleet.sent()[self.since :]

    def writes(self) -> list[dict]:
        return [entry for entry in self.sent() if entry["method"] != "GET"]

    def asked(self) -> list[list[str]]:
        """Each ssh the run started, its words."""
        log = self.ssh / "unrouted.log"
        lines = log.read_text().splitlines()[self.ssh_since :] if log.exists() else []
        return [json.loads(line)["argv"] for line in lines]

    def setting(self, name: str, value: object, *, layer: str = "fleet") -> None:
        """A setting written through its one writer into the layer its row keeps it in (the fleet's settings.yml, or
        the host's), as aw settings writes it."""
        where = paths.home(REPO)
        overrides.set(where, name, value, HOST, getattr(overrides.read(where, HOST), layer).digest)

    def record(self) -> dict:
        """The deploy's job's record."""
        found = [job.read() for job in jobs.listed(paths.jobs_dir(paths.home(REPO)))]
        made = [record for record in found if record["verb"] == "deploy"]
        assert len(made) == 1, found
        return made[0]


@pytest.fixture
def deploying(home, tmp_path, pki):
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        whole_fleet(home, base, directory, pki)
        engine_grant(base, "/vms", "VM.Audit")
        token_id, secret = engine_token(directory)
        found = Fleet(base, directory, Token(token_id, Secret(secret)), pki)
        found.built()
        ssh = refusing_ssh(home)
        log = ssh / "unrouted.log"
        yield Deploying(found, home, ssh, len(found.sent()), len(log.read_text().splitlines()) if log.exists() else 0)


def _ran(ran: subprocess.CompletedProcess[str], status: int) -> str:
    assert ran.returncode == status, f"exit {ran.returncode}\n--- stdout\n{ran.stdout}\n--- stderr\n{ran.stderr}"
    return ran.stdout + ran.stderr


@pytest.mark.parametrize(
    ("planted", "said"),
    [
        pytest.param(
            {"proxmox_template_vmid": FREE},
            f'The base template "{NAME}" is not on the host: no guest has its ID, {FREE}.',
        ),
        pytest.param(
            {"cpu": "kvm64"}, f'The base template "{NAME}" ({TEMPLATE}) differs from its settings: cpu kvm64 →'
        ),
    ],
    ids=["missing", "different"],
)
def test_a_run_with_no_terminal_and_no_root_ssh_refuses_before_anything_is_created(deploying, planted, said) -> None:
    if "cpu" in planted:
        deploying.fleet.plant(cpu=planted["cpu"])
    else:
        deploying.setting("proxmox_template_vmid", FREE, layer="host")
    output = _ran(deploying.deploy(*DEPLOY_TEMPLATE), EXITS["refused"])
    # The report's one sentence: what it has and what is wanted, that this run cannot build it and why, and the line.
    sentence = next(line.removeprefix("error: ") for line in output.splitlines() if line.startswith("error: "))
    assert sentence.startswith(said), sentence
    assert "This run cannot build it on pve: root SSH to the host does not answer with nobody asked (" in sentence
    assert sentence.endswith("and there is no terminal here to ask at. Run aw deploy --only template at a terminal.")
    # Nothing was created: the fake saw no write, and root SSH was asked once, in batch mode, to run nothing.
    assert deploying.writes() == [] and deploying.sent()
    asked = deploying.asked()
    assert len(asked) == 1 and asked[0][-1] == "true" and "BatchMode=yes" in asked[0], asked
    # The same line is NEXT STEPS', and the run held the fleet's lock alone, exclusively: it names no machine.
    record = deploying.record()
    assert record["next"] == [{"machine": None, "matter": "template", "line": sentence}]
    assert record["locks"] == ["fleet:exclusive"]


def test_the_same_template_is_said_and_left_and_root_ssh_never_asked(deploying) -> None:
    output = " ".join(_ran(deploying.deploy(*DEPLOY_TEMPLATE), EXITS["done"]).split())
    assert f'The base template "{NAME}" ({TEMPLATE}) is as its settings would build it.' in output
    assert deploying.writes() == [] and deploying.asked() == []


def test_a_difference_kept_under_rebuild_false_is_said_not_built_and_nothing_cloned(deploying) -> None:
    deploying.fleet.plant(cpu="kvm64")
    deploying.setting("proxmox_template_rebuild", False)
    # The report as the run's summary says it, its lines wrapped.
    output = " ".join(_ran(deploying.deploy(*DEPLOY_TEMPLATE), EXITS["done"]).split())
    assert "differs from its settings: cpu kvm64 → host." in output
    assert (
        "It is kept as it is, since proxmox_template_rebuild is false: with it true, aw deploy --only template"
        in output
    )
    assert deploying.writes() == [] and deploying.asked() == []


def test_the_engine_s_template_with_no_cloud_init_drive_is_never_called_the_same(deploying) -> None:
    # A drive taken off by hand, which the judgement does not read (review 66 of G3, and E12's L4).
    deploying.fleet.plant(delete="ide2")
    output = _ran(deploying.deploy(*DEPLOY_TEMPLATE), EXITS["refused"])
    assert f'The base template "{NAME}" ({TEMPLATE}) has no cloud-init drive' in output
    assert "is as its settings would build it" not in output
    assert f"qm set {TEMPLATE} --ide2 {STORAGE}:cloudinit" in output and "run aw deploy --only template" in output
    assert deploying.writes() == [] and deploying.asked() == []


def test_a_cut_build_s_vm_at_the_id_is_handed_on_as_the_run_s_line_never_built_over(deploying) -> None:
    # alpha, in the pool, is no template: as a build cut before qm template left it.
    deploying.fleet.member(9010)
    deploying.setting("proxmox_template_vmid", 9010, layer="host")
    output = _ran(deploying.deploy(*DEPLOY_TEMPLATE), EXITS["refused"])
    assert f'ID 9010 holds a VM, not the finished template "{NAME}": remove it (qm destroy 9010)' in output
    [step] = deploying.record()["next"]
    assert step["matter"] == "template" and step["line"].startswith("ID 9010 holds a VM")
    assert deploying.writes() == [] and deploying.asked() == []


def test_another_stage_this_card_does_not_build_is_still_refused_by_g3_s_sentence(deploying) -> None:
    assert deploy.not_yet({"only": "template"}) is None
    output = _ran(deploying.deploy("deploy", "--only", "ssh-config"), EXITS["refused"])
    assert "error: aw deploy --only ssh-config is not built yet (card G8 builds it)" in output
    assert deploying.sent() == [] and deploying.asked() == []


class _Unlisted:
    """The engine's client where nothing is listed at the template's ID and the token may read it there: compare finds
    the template missing, by GETs alone, and nothing else is asked of it."""

    def resources(self, kind: str) -> tuple:
        return ()

    def permissions(self, path: str) -> SimpleNamespace:
        return SimpleNamespace(privileges=frozenset({judging.AUDIT}))

    def config(self, *_: object) -> None:
        raise AssertionError("nothing listed is read")


def _ensure(lab: Lab, at: runner.Target, *, terminal: bool) -> template.Compared:
    ctx = SimpleNamespace(job=lab.job, emit=lab.job.emit, cancelled=asyncio.Event())
    host = SimpleNamespace(
        name="lab",
        client=_Unlisted(),
        pool=ENGINE_POOL,
        nic="net0",
        host_flag="",
        layout=hosts.Layout(paths.home(REPO)),
        environ=dict(os.environ),
        fields=SimpleNamespace(ssh_user="root"),
    )
    ensured = template.ensure(ctx, host, lab.settings(), terminal=terminal, target=at)  # type: ignore[arg-type]
    return asyncio.run(ensured)


def test_with_root_ssh_answering_a_run_with_no_terminal_builds_it(lab, monkeypatch) -> None:
    seen: list[list[str]] = []
    real = runner.run

    async def recording(job, at, command, *more, **named):
        seen.append(list(command))
        return await real(job, at, command, *more, **named)

    monkeypatch.setattr(runner, "run", recording)
    built = _ensure(lab, target(lab.host), terminal=False)
    assert (built.outcome, built.identity) == (template.SAME, lab.identity())
    # One connection that runs nothing, then the build's own commands: the template created and converted.
    programs = [next(word for word in command if word != "env" and "=" not in word) for command in seen]
    assert programs[:2] == ["true", "pvesh"], programs
    assert [call[:2] for call in lab.qm_calls()][0] == ["create", str(TEMPLATE)]
    assert ["template", str(TEMPLATE)] in [call[:2] for call in lab.qm_calls()]
    assert lab.job.read()["next"] == []


def test_at_a_terminal_with_no_root_ssh_it_says_what_would_give_one_and_builds_nothing(lab, tmp_path) -> None:
    # An address no stand-in machine has: ssh answers "No route to host" with status 255.
    nowhere = runner.proxmox_host("203.0.113.250", "root", tmp_path / "cp")
    with pytest.raises(template.Refused) as refused:
        _ensure(lab, nowhere, terminal=True)
    sentence = str(refused.value)
    assert not isinstance(refused.value, template.Failed)
    assert sentence.startswith(f'The base template "{NAME}" is not on the host: no guest has its ID, {TEMPLATE}.')
    assert "It is not built: root SSH to lab as root does not answer with nobody asked (" in sentence
    assert "ssh-copy-id" in sentence and "ssh-agent" in sentence and "ssh_identity_file" in sentence
    assert sentence.endswith("then run aw deploy --only template again.") and "at a terminal" not in sentence
    assert lab.qm_calls() == [] and lab.reads() == [] and lab.fetched() == [] and lab.asked() == []
