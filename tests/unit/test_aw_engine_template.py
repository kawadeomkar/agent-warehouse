"""The base template compared with its settings in plain code: lib/claude_vm/engine/template.py, which reads, and
lib/claude_vm/pve/template.py, which judges what was read (card E11, docs/architecture/engine.md §16.4 and §16.5).

What a build makes is wanted(settings), today's role's proxmox_template_wanted as a function: the same fields, the
role's own defaults for the options no row of the settings table names, held equal to its files here, and BUILD
beside it. compare reads the template through the API with the engine's token and says what it found, by the judgement
status calls too (card G16): given what status's own reads give, with the record by pve/record.py's plain parse, it
says what compare says, and it imports nothing of the engine and not the client.

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
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml
from aw_kit import imports_of
from harness import ENGINE_POOL, _pve, _pve_requests, engine_grant, engine_token, fake_fleet
from pki import proxmox_pki

from claude_vm import proxmox
from claude_vm.engine import template
from claude_vm.engine.record import Record
from claude_vm.fleet import overrides, paths
from claude_vm.pve import pin
from claude_vm.pve import record as block
from claude_vm.pve import template as judging
from claude_vm.pve.client import Proxmox, Token
from claude_vm.schema.words import Line
from claude_vm.secret import Secret

REPO = Path(__file__).resolve().parents[2]
# The module's certificate authority, which signs the fake's certificate: cheap enough to make again on each worker
# (tests/unit/conftest.py).
EACH_WORKER = {"pki"}
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
    (the settings' defaults: proxmox_template_vmid 9000, proxmox_node pve and the rest), with changes over them."""
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
    assert "place it in the pool" in _compared(template.UNSEEN, custom=True).said()
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
        by pve/answers.py's parsers) and the record by pve/record.py's plain parse, never engine/record.py's read."""
        base = judging.Base.of(settings, pool=ENGINE_POOL, host_flag="")
        with Proxmox(self.base, pin.kept(self.pki["ca"]), self.token) as client:
            listing, config = client.resources("vm"), client.config("pve", base.template_id)
        fields = block.parse(config.description)
        assert not block.copied(fields or {}, config.identity), "status reads a copy as Copy, never as the VM's own"
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
    found = pooled.compare(_settings(proxmox_template_custom="my-template"))
    assert (found.outcome, found.custom, found.template_id) == (template.UNSEEN, True, None)
    assert "place it in the pool" in found.said()
    pooled.member(9500)
    assert pooled.compare(_settings(proxmox_template_custom="my-template")).usable


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
