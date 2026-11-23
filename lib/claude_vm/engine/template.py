"""The base template, compared with what its settings would build, in plain code (docs/architecture/engine.md §16.4).

Every VM of the fleet is a full clone of the base template, so the template is what each VM starts as. `deploy` and
`status` both ask `compare` whether it is what its settings would build, and get the same answer with the same lines.

`wanted(settings)` is what a build makes, field by field, in the fields today's lib/claude_vm/proxmox.py compares a
template on (`TEMPLATE_FIELDS`): first the settings every clone inherits from it, its CPU type, OS type, bridge and
storage, and the SCSI controller, guest agent, serial port and display every build gives it; then what went into its
disk, which its configuration cannot show and its record keeps: the build steps' version (`BUILD`), the image's URL
and the packages put into the image (none without the guest agent, which is what puts them there). It is the one
definition of a template: the build (card E12) makes one from it, and `compare` holds one to it. The settings are a
run's against one host, every row's value as fleet/overrides.py's `resolve` gives it (`{name: found.value for name,
found in resolve(home, host).items()}`); a role's option that no row names (`OPTIONS`) is read where the settings give
it, as the role options passed through will (card D17), and is otherwise the role's own default, which a test holds
equal to the role's defaults/main.yml.

`compare(client, settings, pool=, host_flag=)` reads the template through the API with the engine's one token, by GETs
alone: the cluster's listing, the guest at the template's ID (its configuration, with the record in its notes), and
where nothing is listed at that ID, what the token may read there. It says one of `OUTCOMES`:

- `missing`: no guest has the template's ID, which is said only where the token may read that ID, so that a guest
  there would have been listed: with `proxmox_audit_scope: node`, its default, the token holds `VM.Audit` on `/vms`
  (card E1). What the token holds is asked of Proxmox itself (GET /access/permissions, which needs no privilege), not
  read off the setting, so a token that holds less than its setting says reads `unseen`, never `missing`.
- `unseen`: nothing is listed at the template's ID, and the token may not read that ID: with `proxmox_audit_scope:
  pool` a guest outside the pool is invisible, so missing cannot be told from another's guest, and the build asks the
  host over root SSH before it makes anything (card E12).
- `same`: this engine's template, as its settings would build it.
- `different`: this engine's template, with a line for each difference, `<field> <has> → <wanted>`, from today's
  `proxmox_template_drift`, which stays where it is. Under `proxmox_template_rebuild: false` a difference is only said,
  with the `deploy --only template` line, and the template is kept.
- `refused`: the guest at the template's ID is not this project's build, or is where no clone could use it, said in
  one sentence with the way on, as today's role refuses it, and left as it is, never rebuilt: a container; a guest
  that is no member of the fleet's pool; a template on another node than `proxmox_node`; a VM that is no template; a
  template of another name; and a template whose notes hold no record of this engine's build, one it cannot read, a
  copy of another VM's (engine/record.py's `Copy`), or a record of another kind.
- `custom`: `proxmox_template_custom` names a template of the person's own, which is only read and cloned, never
  compared or rebuilt, and held by today's `custom_template_problems` to what a clone needs: `why` holds each thing it
  lacks, with what fixes it, and then nothing is made from it. Where the token may not read the guests outside its
  pool and none of that name is listed, it is `unseen` too.

A template is this engine's only where its notes hold engine/record.py's record of kind `template`, written for that
template (its own UUID), and it is a member of the pool: anything else at the ID is refused and left, so that no VM of
the new engine stands on a template it may not rebuild, and a person's own template at the ID is never destroyed. A
person's own template is cloned only where `proxmox_template_custom` names it. The image's digest the record keeps is
said nowhere and never compared, as today.

The token is the client's alone, and nothing here sends any request but a GET (tests/unit/test_aw_engine_template.py).
Proxmox's API paths are the client's: the one path written here is an ACL path, the template's ID under `/vms`, which
GET /access/permissions takes as its query.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from .. import proxmox
from ..pve.answers import Resource, VmConfig
from ..pve.client import Proxmox
from ..schema.printer import said
from ..schema.words import Line
from ..text import one_line
from . import record

__all__ = ["BUILD", "OPTIONS", "OUTCOMES", "Compared", "compare", "wanted"]

# The build steps' own version. Raise it whenever a change to them changes what a template is - a qm option, a step of
# the image tool, a package the build adds - and every template built before is rebuilt by the next deploy.
BUILD = 1

# The settings every build gives a template, which no setting changes, as today's role gives them
# (roles/proxmox_template/vars/main.yml).
SCSIHW = "virtio-scsi-pci"
SERIAL0 = "socket"
VGA = "serial0"
AGENT = "1"

# The roles' options a build reads that no row of the settings table names, each with its role's own default, which a
# test holds equal to roles/proxmox_template/defaults/main.yml and roles/proxmox_vm/defaults/main.yml. The template's
# bridge and storage are the rows proxmox_bridge and proxmox_storage unless their options name others, as today's
# inventory wires them.
OPTIONS: Mapping[str, object] = {
    "proxmox_template_cpu_type": "host",
    "proxmox_template_ostype": "l26",
    "proxmox_template_image_url": (
        "https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img"
    ),
    "proxmox_template_install_guest_agent": True,
    "proxmox_template_customize_packages": ("qemu-guest-agent",),
    # The boot disk a VM is resized as, and a template of the person's own is held to having.
    "proxmox_vm_disk": "scsi0",
}
# The options that fall back to a row of the table, by the row.
FROM_ROWS = {"proxmox_template_bridge": "proxmox_bridge", "proxmox_template_storage": "proxmox_storage"}

# What compare says of the template.
MISSING, UNSEEN, SAME, DIFFERENT, REFUSED, CUSTOM = OUTCOMES = (
    "missing",
    "unseen",
    "same",
    "different",
    "refused",
    "custom",
)

# The privilege a token must hold at a guest's ACL path to see it listed and read it.
AUDIT = "VM.Audit"
# The ACL path every guest's own is under, and what reaches all of them.
GUESTS = "/vms"
# The most characters of a name Proxmox gave that a sentence says, and of a line of a difference.
NAME_MOST = 100
LINE_MOST = 300


def _plain(text: str, most: int = NAME_MOST) -> str:
    """What Proxmox gave, made one line of plain text before a sentence says it: a guest's name, its node, a
    difference's line."""
    return one_line(text, most)


def _option(settings: Mapping[str, object], name: str) -> object:
    """A role's option: the settings' value where they give one, else its row's (FROM_ROWS), else its role's default."""
    if name in settings:
        return settings[name]
    if name in FROM_ROWS:
        return settings[FROM_ROWS[name]]
    return OPTIONS[name]


def wanted(settings: Mapping[str, object]) -> dict[str, str]:
    """What a build makes, by today's TEMPLATE_FIELDS, each as a string as proxmox_template_drift compares it: the
    settings every clone inherits, then the build's version, the image's URL and the packages put into the image,
    comma-joined, none without the guest agent."""
    if not isinstance(settings, Mapping):
        raise TypeError("the settings are a mapping of each setting's name to its value")
    packages = _option(settings, "proxmox_template_customize_packages")
    if not isinstance(packages, (list, tuple)) or not all(isinstance(package, str) for package in packages):
        raise TypeError("proxmox_template_customize_packages is a list of packages' names")
    agent = _option(settings, "proxmox_template_install_guest_agent") is True
    made = {
        "cpu": str(_option(settings, "proxmox_template_cpu_type")),
        "ostype": str(_option(settings, "proxmox_template_ostype")),
        "bridge": str(_option(settings, "proxmox_template_bridge")),
        "storage": str(_option(settings, "proxmox_template_storage")),
        "scsihw": SCSIHW,
        "agent": AGENT,
        "serial0": SERIAL0,
        "vga": VGA,
        "build": str(BUILD),
        "image": str(_option(settings, "proxmox_template_image_url")),
        "packages": ",".join(packages) if agent else "",
    }
    return {field: made[field] for field in proxmox.TEMPLATE_FIELDS}


@dataclasses.dataclass(frozen=True)
class Compared:
    """What compare found: its outcome (one of OUTCOMES); the template's name and its ID (a custom template's where
    one was found, else None); the node VMs are cloned on and the fleet's pool; for different, a line for each
    difference and whether rebuilding is on; for refused, and custom where it lacks something, why, each a sentence;
    the line deploy --only template is said with where a difference is kept; and whether the template is one of the
    person's own, which proxmox_template_custom names."""

    outcome: str
    name: str
    template_id: int | None
    node: str
    pool: str
    differences: tuple[str, ...] = ()
    why: tuple[str, ...] = ()
    rebuild: bool = True
    line: Line | None = None
    custom: bool = False

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"a comparison's outcome is one of {', '.join(OUTCOMES)}")

    @property
    def usable(self) -> bool:
        """Whether a VM may be cloned from it as it is: this engine's template as its settings would build it, or a
        template of the person's own that lacks nothing a clone needs."""
        return self.outcome == SAME or (self.outcome == CUSTOM and not self.why)

    @property
    def refused(self) -> bool:
        """Whether it is refused, and nothing is to be built over it or cloned from it: refused, or a template of the
        person's own that lacks something."""
        return self.outcome == REFUSED or (self.outcome == CUSTOM and bool(self.why))

    def said(self) -> str:
        """One sentence a person reads, which status and NEXT STEPS say as it is."""
        at = f'The base template "{self.name}" ({self.template_id})'
        if self.outcome == MISSING:
            return f'The base template "{self.name}" is not on the host: no guest has its ID, {self.template_id}.'
        if self.outcome == SAME:
            return f"{at} is as its settings would build it."
        if self.outcome == DIFFERENT:
            differs = f"{at} differs from its settings: {'; '.join(self.differences)}."
            if self.rebuild:
                return differs
            # The line said apart, so that nothing Proxmox or the record gave is read as a sentence's field.
            line = said("{line}", {"line": self.line})
            kept = "It is kept as it is, since proxmox_template_rebuild is false: with it true,"
            return f"{differs} {kept} {line} rebuilds it."
        if self.outcome == UNSEEN and self.custom:
            return (
                f'The template "{self.name}" (proxmox_template_custom) is not among the guests the engine\'s token may '
                f"read, which are its pool {self.pool}'s alone (proxmox_audit_scope: pool): place it in the pool, "
                "where the token may read it."
            )
        if self.outcome == UNSEEN:
            return (
                f'No guest with the ID of the base template "{self.name}", {self.template_id}, is among those the '
                f"engine's token may read, and it may read none outside the pool {self.pool} (proxmox_audit_scope: "
                "pool): whether that ID is free cannot be told through the API, so a build asks the host over root SSH "
                "first."
            )
        if self.outcome == CUSTOM and not self.why:
            return f'The template "{self.name}" ({self.template_id}), yours, is only read and cloned.'
        if self.outcome == CUSTOM:
            return (
                f'The template "{self.name}" (proxmox_template_custom) cannot be cloned: {"; ".join(self.why)}. '
                "Nothing is made from it (docs/guides/base-image.md)."
            )
        return " ".join(self.why)


def _at(listing: tuple[Resource, ...], template_id: int) -> Resource | None:
    """The guest the cluster lists at template_id, any kind, or None."""
    return next((entry for entry in listing if entry.vmid == template_id), None)


def _reads(client: Proxmox, path: str) -> bool:
    """Whether the token may read the guest at an ACL path, or every guest at GUESTS: what Proxmox says it holds there,
    so that a guest it could not see is never said to be missing."""
    return AUDIT in client.permissions(path).privileges


def _what(entry: Resource) -> str:
    """A guest as the listing names it, for a sentence."""
    kind = "a template" if entry.template else "a VM"
    return f'{kind} named "{_plain(entry.name)}"' if entry.name else "a guest"


def _recorded(found: record.Record) -> str:
    """The record's template fields in the one line today's proxmox_template_drift reads them from: the build's
    version, the image's URL, the packages and the image's digest, which it keeps and never compares."""
    return proxmox.proxmox_template_record(
        {
            "build": "" if found.build is None else found.build,
            "image": found.image_url,
            "packages": ",".join(found.packages),
            "sha256": found.image,
        }
    )


def _differences(config: VmConfig, found: record.Record, want: dict[str, str]) -> tuple[str, ...]:
    """How the template differs from want, by today's proxmox_template_drift: its qm settings read from its
    configuration, so a change made in Proxmox's page is seen, and what went into its disk from its record."""
    drift = proxmox.proxmox_template_drift({**config.values, "description": _recorded(found)}, want)
    return tuple(_plain(line, LINE_MOST) for line in drift)


def _custom(
    client: Proxmox, settings: Mapping[str, object], listing: tuple[Resource, ...], name: str, node: str, pool: str
) -> Compared:
    """A template of the person's own, which proxmox_template_custom names: held by custom_template_problems, reading
    the configuration of the one template of that name on the node."""
    named = [entry for entry in listing if entry.type == "qemu" and entry.name == name]
    if not named and not _reads(client, GUESTS):
        return Compared(UNSEEN, name, None, node, pool, custom=True)
    template_id, configs = None, {}
    if len(named) == 1 and named[0].template and named[0].node == node:
        template_id = named[0].vmid
        configs[str(template_id)] = client.config(node, template_id).values
    problems = proxmox.custom_template_problems(
        [entry.entry for entry in listing],
        configs,
        name,
        node,
        str(_option(settings, "proxmox_vm_disk")),
        str(settings["proxmox_nic"]),
        # No tag is the fleet's: what is the fleet's is its pool's members with a record, and its one label decides
        # nothing (vm_label).
        (),
    )
    return Compared(CUSTOM, name, template_id, node, pool, why=tuple(problems), custom=True)


def compare(client: Proxmox, settings: Mapping[str, object], *, pool: str, host_flag: str) -> Compared:
    """Whether the base template on the host client reaches is what settings would build, by GETs alone: see the
    module's docstring for each outcome. settings are the run's against that host, every row's value (the module's
    docstring); pool is the fleet's pool there, which every guest of the fleet is a member of; host_flag is the host a
    line names, as the printer of a command takes it ("" where the fleet has one host). A refusal of Proxmox's, the
    client's errors, is raised as it is."""
    if not isinstance(pool, str) or not pool:
        raise ValueError("compare is given the fleet's pool on the host")
    if not isinstance(host_flag, str):
        raise TypeError("compare is given the host's flag a line names, '' for none")
    node = str(settings["proxmox_node"])
    listing = client.resources("vm")
    custom = str(settings["proxmox_template_custom"] or "")
    if custom:
        return _custom(client, settings, listing, custom, node, pool)
    template_id = settings["proxmox_template_vmid"]
    if isinstance(template_id, bool) or not isinstance(template_id, int):
        raise TypeError("proxmox_template_vmid is a whole number")
    name = str(settings["proxmox_template_name"])
    rebuild = settings["proxmox_template_rebuild"] is True
    line = Line("deploy", {"only": "template"}, host_flag=host_flag)
    missing = Compared(MISSING, name, template_id, node, pool, rebuild=rebuild, line=line)

    def refuse(why: str) -> Compared:
        return dataclasses.replace(missing, outcome=REFUSED, why=(why,))

    entry = _at(listing, template_id)
    if entry is None:
        return missing if _reads(client, f"{GUESTS}/{template_id}") else dataclasses.replace(missing, outcome=UNSEEN)
    other = "choose another proxmox_template_vmid for the base template"
    if entry.type != "qemu":
        return refuse(
            f'ID {template_id} holds a container, not the base template "{name}": it is left as it is; {other}.'
        )
    if entry.pool != pool:
        return refuse(
            f"ID {template_id} holds {_what(entry)}, which is no member of the pool {pool}, so it is not this fleet's: "
            f"it is left as it is; {other}."
        )
    if entry.node != node:
        return refuse(
            f'The base template "{name}" ({template_id}) is on node {_plain(entry.node)}, but proxmox_node is {node}, '
            "and a VM can only be cloned on the node that holds its template: set proxmox_node to "
            f"{_plain(entry.node)}, or give the "
            f"template on {node} an ID and a name of its own (proxmox_template_vmid, proxmox_template_name)."
        )
    # Whether it is a template, and its name, from its own configuration: never the listing's, which lags what a run
    # just did by up to ten seconds, as the build's own last look at it would meet.
    config = client.config(node, template_id)
    if not config.template or config.name != name:
        what = f'a template named "{_plain(config.name)}"' if config.template else "a VM"
        return refuse(
            f'ID {template_id} holds {what}, not the finished template "{name}": remove it (qm destroy {template_id}) '
            "or choose another proxmox_template_vmid."
        )
    try:
        found = record.read(config)
    except record.Unreadable as unreadable:
        return refuse(f'The template "{name}" ({template_id}) is never rebuilt or cloned: {unreadable}.')
    if found is None:
        return refuse(
            f'The template "{name}" ({template_id}) keeps no record of this engine\'s build, so it is never rebuilt, '
            "which would destroy it, nor cloned: if it is yours, name it in proxmox_template_custom to clone it as it "
            "is; otherwise choose another proxmox_template_vmid and proxmox_template_name."
        )
    if found.kind != "template":
        return refuse(
            f'The template "{name}" ({template_id}) holds the record of a {found.kind} of this engine\'s, not of its '
            f"base template: it is left as it is; {other}."
        )
    differences = _differences(config, found, wanted(settings))
    return dataclasses.replace(missing, outcome=DIFFERENT if differences else SAME, differences=differences)
