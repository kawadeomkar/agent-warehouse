"""The base template judged against what its settings would build, from what was read of it: plain code with no
client, by which `deploy` and `status` judge alike (docs/architecture/engine.md §16.5).

Every VM of the fleet is a full clone of the base template, so the template is what each VM starts as.
engine/template.py's `compare` reads the template through the API with the engine's one token and judges what it read
here; `status`, which may not import the engine (card G15), reads it through its own client and judges it by these same
functions (card G16), so both say the same outcome in the same lines, the record's UUID held here whichever reader read
it, and each field's form by the reader (below). Nothing here reads or sends anything: it imports nothing of engine/
and not the client (tests/unit/test_aw_engine_template.py).

`wanted(settings)` is what a build makes, field by field, in the fields today's lib/claude_vm/proxmox.py compares a
template on (`TEMPLATE_FIELDS`): first the settings every clone inherits from it, its CPU type, OS type, bridge and
storage, and the SCSI controller, guest agent, serial port and display every build gives it; then what went into its
disk, which its configuration cannot show and its record keeps: the build steps' version (`BUILD`), the image's URL
and the packages put into the image (none without the guest agent, which is what puts them there). It is the one
definition of a template: the build (card E12) makes one from it, and the judgement holds one to it. The settings are a
run's against one host, every row's value as fleet/overrides.py's `resolve` gives it (`{name: found.value for name,
found in resolve(home, host).items()}`); a role's option that no row names (`OPTIONS`) is read where the settings give
it, as fleet/passthrough.py passes such an option through (card D17), and is otherwise the role's own default, which
a test holds equal to the role's defaults/main.yml.

`judge(base, entry, config, found, want, readable=)` says what the guest at the base template's ID is: `base` the
template the settings name (`Base.of`), `entry` its entry in the cluster's listing (None where none is listed at the
ID, `at`), `config` its configuration, `found` its record and `want` what a build makes. `found` is what reading the
record gave, by either reader: its fields, as pve/record.py's `parse` gives them or engine/record.py's `Record.fields`
renders them, None where the notes hold no record of this engine's, or the Unreadable the read raised, a `Copy` among
them. Whichever reader made it, `judge` holds the record to the guest's own UUID itself, by pve/record.py's rules: one
that names none (only a VM's first form may, `first_form`) or another guest's (a copy, `copied`) is refused with the
sentence engine/record.py's `read` gives it. Each field's form is the reader's: engine/record.py's `read` holds every
one; `parse` holds a block's shape alone, so a value of the wrong form that it passes is judged as it stands.

`listed(base, entry, readable=)` is what the listing alone says, and None where the guest's configuration is to be
read, so that a reader reads a configuration only where it is judged: a qemu guest at the ID, in the fleet's pool, on
`proxmox_node`. `custom(listing, config, ...)` judges a template of the person's own. Each says one of `OUTCOMES`, in a
`Compared`:

- `missing`: no guest has the template's ID, which is said only where the token may read that ID (`readable`), so
  that a guest there would have been listed: with `proxmox_audit_scope: node`, its default, the token holds `VM.Audit`
  on `/vms` (card E1). What the token holds is asked of Proxmox itself (GET /access/permissions at `/vms/<ID>`, which
  needs no privilege), not read off the setting, so a token that holds less than its setting says reads `unseen`,
  never `missing`.
- `unseen`: nothing is listed at the template's ID, and the token may not read that ID: with `proxmox_audit_scope:
  pool` a guest outside the pool is invisible, so missing cannot be told from another's guest, and the build asks the
  host over root SSH before it makes anything (card E12).
- `same`: this engine's template, as its settings would build it.
- `different`: this engine's template, with a line for each difference, `<field> <has> → <wanted>`, from today's
  `proxmox_template_drift`, which stays where it is. Under `proxmox_template_rebuild: false` a difference is only said,
  with the `deploy --only template` line, and the template is kept and cloned from as it is, as today's deploy clones
  from it (`usable`).
- `refused`: the guest at the template's ID is not this project's build, or is where no clone could use it, said in
  one sentence with the way on, as today's role refuses it, and left as it is, never rebuilt: a container; a guest
  that is no member of the fleet's pool; a template on another node than `proxmox_node`; a VM that is no template; a
  template of another name; and a template whose notes hold no record of this engine's build, one it cannot read, a
  copy of another VM's, or a record of another kind. Whether it is a template, and its name, are read from its own
  configuration, never the listing's, which lags what a run just did by up to ten seconds, as the build's own last look
  at it would meet.
- `custom`: `proxmox_template_custom` names a template of the person's own, which is only read and cloned, never
  compared or rebuilt, and held by today's `custom_template_problems` to what a clone needs: `why` holds each thing it
  lacks, with what fixes it, and then nothing is made from it. Where the token may not read the guests outside its
  pool and none of that name is listed, it is `unseen` too.

A template is this engine's only where its notes hold engine/record.py's record of kind `template`, written for that
template (its own UUID, which a template's record names from its first write: only a VM's first form names none), and
it is a member of the pool: anything else at the ID is refused and left, so that no VM of the new engine stands on a
template it may not rebuild, and a person's own template at the ID is never destroyed. A person's own template is
cloned only where `proxmox_template_custom` names it. The image's digest the record keeps is said nowhere and never
compared, as today.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from .. import proxmox
from ..schema.printer import said
from ..schema.words import Line
from ..text import one_line
from .answers import Resource, VmConfig
from .record import NO_UUID, Copy, Unreadable, copied, first_form

__all__ = [
    "BUILD",
    "OPTIONS",
    "OUTCOMES",
    "Base",
    "Compared",
    "at",
    "custom",
    "custom_id",
    "judge",
    "listed",
    "named",
    "option",
    "wanted",
]

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

# What the judgement says of the template.
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


def option(settings: Mapping[str, object], name: str) -> object:
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
    packages = option(settings, "proxmox_template_customize_packages")
    if not isinstance(packages, (list, tuple)) or not all(isinstance(package, str) for package in packages):
        raise TypeError("proxmox_template_customize_packages is a list of packages' names")
    agent = option(settings, "proxmox_template_install_guest_agent") is True
    made = {
        "cpu": str(option(settings, "proxmox_template_cpu_type")),
        "ostype": str(option(settings, "proxmox_template_ostype")),
        "bridge": str(option(settings, "proxmox_template_bridge")),
        "storage": str(option(settings, "proxmox_template_storage")),
        "scsihw": SCSIHW,
        "agent": AGENT,
        "serial0": SERIAL0,
        "vga": VGA,
        "build": str(BUILD),
        "image": str(option(settings, "proxmox_template_image_url")),
        "packages": ",".join(packages) if agent else "",
    }
    return {field: made[field] for field in proxmox.TEMPLATE_FIELDS}


@dataclasses.dataclass(frozen=True)
class Compared:
    """What the judgement found: its outcome (one of OUTCOMES); the template's name and its ID (a custom template's
    where one was found, else None); the node VMs are cloned on and the fleet's pool; for different, a line for each
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
        """Whether a VM may be cloned from it as it is: this engine's template as its settings would build it, or one
        whose differences are kept under proxmox_template_rebuild: false, as today's deploy clones from it, or a
        template of the person's own that lacks nothing a clone needs."""
        return (
            self.outcome == SAME
            or (self.outcome == DIFFERENT and not self.rebuild)
            or (self.outcome == CUSTOM and not self.why)
        )

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


@dataclasses.dataclass(frozen=True)
class Base:
    """The base template the settings name, and where it is judged: its name and ID, the node VMs are cloned on, the
    fleet's pool, whether a difference rebuilds it (proxmox_template_rebuild), and the deploy --only template line, for
    the host a line names."""

    name: str
    template_id: int
    node: str
    pool: str
    rebuild: bool
    line: Line

    @classmethod
    def of(cls, settings: Mapping[str, object], *, pool: str, host_flag: str) -> Base:
        """The base template settings name: the run's against one host, every row's value (the module's docstring);
        pool is the fleet's pool there, and host_flag the host a line names, as the printer of a command takes it (""
        where the fleet has one host)."""
        template_id = settings["proxmox_template_vmid"]
        if isinstance(template_id, bool) or not isinstance(template_id, int):
            raise TypeError("proxmox_template_vmid is a whole number")
        return cls(
            name=str(settings["proxmox_template_name"]),
            template_id=template_id,
            node=str(settings["proxmox_node"]),
            pool=pool,
            rebuild=settings["proxmox_template_rebuild"] is True,
            line=Line("deploy", {"only": "template"}, host_flag=host_flag),
        )

    def compared(self, outcome: str, **more: object) -> Compared:
        """What the judgement found of this template: outcome, with more of Compared's fields."""
        return Compared(
            outcome, self.name, self.template_id, self.node, self.pool, rebuild=self.rebuild, line=self.line, **more
        )

    def refused(self, why: str) -> Compared:
        return self.compared(REFUSED, why=(why,))


def at(listing: tuple[Resource, ...], template_id: int) -> Resource | None:
    """The guest the cluster lists at template_id, any kind, or None."""
    return next((entry for entry in listing if entry.vmid == template_id), None)


def _what(entry: Resource) -> str:
    """A guest as the listing names it, for a sentence."""
    kind = "a template" if entry.template else "a VM"
    return f'{kind} named "{_plain(entry.name)}"' if entry.name else "a guest"


def listed(base: Base, entry: Resource | None, *, readable: bool) -> Compared | None:
    """What the cluster's listing alone says of the guest at base's ID, entry (None where none is listed there):
    missing where none is and the token may read that ID (readable), unseen where it may not; refused for a container,
    a guest that is no member of the pool, or one on another node; None where the guest's configuration is to be read
    and judged (judge)."""
    if entry is None:
        return base.compared(MISSING if readable else UNSEEN)
    other = "choose another proxmox_template_vmid for the base template"
    if entry.type != "qemu":
        return base.refused(
            f'ID {base.template_id} holds a container, not the base template "{base.name}": it is left as it is; '
            f"{other}."
        )
    if entry.pool != base.pool:
        return base.refused(
            f"ID {base.template_id} holds {_what(entry)}, which is no member of the pool {base.pool}, so it is not "
            f"this fleet's: it is left as it is; {other}."
        )
    if entry.node != base.node:
        return base.refused(
            f'The base template "{base.name}" ({base.template_id}) is on node {_plain(entry.node)}, but proxmox_node '
            f"is {base.node}, and a VM can only be cloned on the node that holds its template: set proxmox_node to "
            f"{_plain(entry.node)}, or give the "
            f"template on {base.node} an ID and a name of its own (proxmox_template_vmid, proxmox_template_name)."
        )
    return None


# A record's fields, as pve/record.py's parse gives them (a value a str, a list a tuple) or engine/record.py's
# Record.fields renders them (a field not set None).
Fields = Mapping[str, str | tuple[str, ...] | None]


def _recorded(found: Fields) -> str:
    """The record's template fields in the one line today's proxmox_template_drift reads them from: the build's
    version, the image's URL, the packages and the image's digest, which it keeps and never compares."""
    return proxmox.proxmox_template_record(
        {
            "build": found.get("build") or "",
            "image": found.get("image_url") or "",
            "packages": ",".join(found.get("packages") or ()),
            "sha256": found.get("image") or "",
        }
    )


def _differences(config: VmConfig, found: Fields, want: Mapping[str, str]) -> tuple[str, ...]:
    """How the template differs from want, by today's proxmox_template_drift: its qm settings read from its
    configuration, so a change made in Proxmox's page is seen, and what went into its disk from its record."""
    drift = proxmox.proxmox_template_drift({**config.values, "description": _recorded(found)}, dict(want))
    return tuple(_plain(line, LINE_MOST) for line in drift)


def judge(
    base: Base,
    entry: Resource | None,
    config: VmConfig | None,
    found: Fields | Unreadable | None,
    want: Mapping[str, str],
    *,
    readable: bool = False,
) -> Compared:
    """What the guest at base's ID is, from its listing entry, its configuration and its record, against want (what
    a build makes, wanted): listed's verdict where the listing alone decides, else the configuration's and the
    record's. config is needed only where listed gives None, and found is what reading its record gave, by either
    reader (the module's docstring), held here to the guest's own UUID; readable, whether the token may read the ID,
    only where nothing is listed."""
    decided = listed(base, entry, readable=readable)
    if decided is not None:
        return decided
    if config is None:
        raise ValueError("a guest the listing names at the base template's ID is judged by its configuration")
    if not config.template or config.name != base.name:
        what = f'a template named "{_plain(config.name)}"' if config.template else "a VM"
        return base.refused(
            f'ID {base.template_id} holds {what}, not the finished template "{base.name}": remove it (qm destroy '
            f"{base.template_id}) or choose another proxmox_template_vmid."
        )
    named = f'The template "{base.name}" ({base.template_id})'
    # Whichever reader made the record, it is this guest's only where it names the guest's own UUID: one that names
    # none, but a VM's first form, or another's, a copy, is refused as engine/record.py's read refuses it.
    whose = f"VM {config.vmid}'s"
    if isinstance(found, Mapping) and not found.get("uuid") and not first_form(found):
        found = Unreadable(NO_UUID, whose)
    elif isinstance(found, Mapping) and copied(found, config.identity):
        found = Copy(whose)
    if isinstance(found, Unreadable):
        return base.refused(f"{named} is never rebuilt or cloned: {found}.")
    if found is None:
        return base.refused(
            f"{named} keeps no record of this engine's build, so it is never rebuilt, which would destroy it, nor "
            "cloned: if it is yours, name it in proxmox_template_custom to clone it as it is; otherwise choose another "
            "proxmox_template_vmid and proxmox_template_name."
        )
    kind = found.get("kind")
    if kind != "template":
        return base.refused(
            f"{named} holds the record of a {_plain(str(kind))} of this engine's, not of its base template: it is left "
            "as it is; choose another proxmox_template_vmid for the base template."
        )
    differences = _differences(config, found, want)
    return base.compared(DIFFERENT if differences else SAME, differences=differences)


def named(listing: tuple[Resource, ...], name: str) -> tuple[Resource, ...]:
    """The VMs and templates the cluster lists by name."""
    return tuple(entry for entry in listing if entry.type == "qemu" and entry.name == name)


def custom_id(listing: tuple[Resource, ...], name: str, node: str) -> int | None:
    """The ID of the template of the person's own whose configuration it is held by: the one VM or template the listing
    names name, where it is a template on node; None otherwise, where the listing alone says what it lacks."""
    found = named(listing, name)
    if len(found) == 1 and found[0].template and found[0].node == node:
        return found[0].vmid
    return None


def custom(
    listing: tuple[Resource, ...],
    config: VmConfig | None,
    *,
    name: str,
    node: str,
    pool: str,
    disk: str,
    nic: str,
    readable: bool,
) -> Compared:
    """A template of the person's own, named name, which proxmox_template_custom names: held by today's
    custom_template_problems to what a clone needs, from the listing and the configuration of the template custom_id
    names (config, needed only where it names one); disk and nic are the boot disk and network card a VM is resized and
    addressed by. Unseen where none of that name is listed and the token may not read every guest (readable)."""
    if not named(listing, name) and not readable:
        return Compared(UNSEEN, name, None, node, pool, custom=True)
    template_id = custom_id(listing, name, node)
    if template_id is not None and config is None:
        raise ValueError("a template of the person's own is held by its configuration")
    problems = proxmox.custom_template_problems(
        [entry.entry for entry in listing],
        {} if template_id is None or config is None else {str(template_id): config.values},
        name,
        node,
        disk,
        nic,
        # No tag is the fleet's: what is the fleet's is its pool's members with a record, and its one label decides
        # nothing (vm_label).
        (),
    )
    return Compared(CUSTOM, name, template_id, node, pool, why=tuple(problems), custom=True)
