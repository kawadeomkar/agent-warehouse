"""The role options passed through: every typed option of the roles that no row of the settings table names, held to
its role's own meta/argument_specs.yml and handed to the role variable of the same name unchanged
(docs/architecture/engine.md §16.9).

A person may set such an option under the role's own name, as today's settings verb lets them: in the fleet's
settings.yml at its top level, one value for the whole fleet (home fleet, scope global), or for one run with --set.
fleet/overrides.py takes its name where it takes a row's. No row is made for it and no fate is entered for it by hand:
the role's spec is the one source of its type, its choices and its description, and an option set nowhere is no
value here, so the role's own default applies where the role runs. Where layer 1 runs the option's role, the value is
handed to the role variable of the same name; where a role's work moved into layer 2 or into the engine, the step
that took it reads the option by its name.

`options()` returns every typed option of the roles that no row names - neither a row's name nor reached by a row's
setting through the wiring - with its role, the entry point that first lists it, the spec's type, choices, list
elements, no_log and description, and `refused`: None for one passed through, else why it is no setting, as today's
tools/settings refuses it, in the words a refusal says after its name: one the spec marks no_log, or that a name the
vault holds reaches, is the vault's; one made from the machine's name is each machine's own; one a run sets for itself
(`BY_A_RUN`); and `LEFT_OUT`, each with its reason. `refusal(name)` says why any other name of a role's option is no
setting too: an option a row's setting reaches is set through that row alone (vm_cores, for proxmox_vm_cores), and one
whose spec takes any value has no type to hold a value to. `typed(option, text)` holds typed text to the option's spec
as overrides.typed holds text to a row: its type, its choices, the elements of a list, no expression and no control
character; a path is one on the machine the role runs on, kept as typed, never made this computer's. `row(name)` is the
row overrides.py reads an option passed through by. An option `TEXT` names, whose value is text of several lines by
what it is (an armored key, a whole CLAUDE.md), is read by a row of type text: line breaks and no other control
character, kept in the fleet's settings.yml as YAML's block holds it, and never typed.

Which setting reaches which role variable is today's wiring (lib/claude_vm/wiring.py: the roles' defaults and the
project's) until the cutover, when the guest's own table of the role variables takes its place. Standard library, the
table and today's wiring at import; the YAML reader is imported by the functions that read the roles' files.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from claude_vm import wiring
from claude_vm.fleet import registry
from claude_vm.fleet.registry import Row

# Each type a role's spec gives an option, as the row overrides.py holds its value to. A path is one on the machine
# the role runs on, so it is held as one line of text and kept as typed: the table's path is this computer's, whose ~
# would be made this computer's home. A list is of strings, the one kind of list the roles take.
ROW_TYPES = MappingProxyType(
    {"bool": "bool", "int": "int", "str": "str", "path": "str", "list": "list[str]", "dict": "dict"}
)
# The options whose value is text of several lines by what it is, each with why: a string of the spec read by a row of
# type text, which may hold line breaks and no other control character, kept in the fleet's settings.yml as YAML's
# block holds it and never typed (fleet/overrides.py). Every other string is one line.
TEXT = MappingProxyType(
    {
        "proxmox_template_image_key": "an armored OpenPGP key, the one a cloud image's list of digests is signed by",
        "claude_code_global_instructions": "a whole CLAUDE.md, the instructions Claude Code applies in every project",
    }
)
# The entry point a role runs by default, which comes first where an option is listed by several.
MAIN = "main"
# Whose a value made from the machine's name is: the name a role's default reads it from.
MACHINE_NAME = "inventory_hostname"
# A name the vault holds starts so, in today's wiring.
VAULT_NAME = "vault_"

# Why an option is no setting, as a refusal says it after the option's name. THROUGH names the row.
THROUGH = "is set through {row}, a setting of its own: set {row} instead"
SECRET = "is a secret, which the vault holds: it is no setting"
PER_MACHINE = "is made from each machine's own name: one setting would give every machine the same"
SET_BY_A_RUN = "is set by a run for itself: it is no setting"
ANY_VALUE = "takes any value, which no type holds: it is no setting"

# The options a run sets for itself, as today's tools/settings finds them in the playbooks and the recipes: each
# sign-in's follow-up, question and whether a person is at the screen, the golden template's build, a VM's state, its
# start, its size, its repositories and the template it is cloned from, a node's join that Proxmox recorded, and the
# other VMs a release reads. The raw one among them, the VM's ID, takes any value and is refused for that.
BY_A_RUN = frozenset(
    {
        "claude_code_sign_in_follows",
        "claude_code_login_interactive",
        "github_projects_sign_in_follows",
        "github_projects_login_interactive",
        "github_projects_login_ask",
        "github_projects_login_role_follows",
        "proxmox_template_drift_config",
        "proxmox_template_golden_target",
        "proxmox_template_golden_vm",
        "proxmox_template_golden_vm_disk",
        "proxmox_template_golden_digest",
        "proxmox_template_golden_shared",
        "proxmox_vm_template_vmid",
        "proxmox_vm_state",
        "proxmox_vm_start_existing",
        "proxmox_vm_size",
        "proxmox_vm_repos",
        "tailscale_joined_before",
        "tailscale_sign_in_follows",
        "tailscale_login_interactive",
        "tailscale_login_ask",
        "tailscale_release_other_vms",
    }
)

# Why an option of the sign-ins' and the VPN's roles is no setting: each line that names a machine names its host as the
# printer of a command writes it, and how a machine is reached is the route's, which the SSH runner takes.
HOST_FLAG = "is set by the run: a line that names a machine names its host as the printer of a command writes it"
SSH_VIA = "is set by the run: how a machine is reached is the route's, which the SSH runner takes"
# The options left out by name, each with why, as a refusal says it after the name: the host's connection, which is a
# field of each host's host.yml or the anchor beside it, and a switch the engine has not, since it always checks the
# API's certificate (the four the table's connection rows fed today, which left it, and the address and the anchor); the
# client's own bound on a call; what owns a VM, which is the fleet's pool and the VM's record, never a tag; the two the
# run sets from vpn, the one setting that chooses the VPN, and the one that names vpn in its messages; what a run
# chooses or a role sets for itself (the template a VM is cloned from, the golden's build VM), and a folder of this
# computer's; what each machine's record keeps (its own repositories, whether its Tailscale had a login); the lines that
# name a machine's host and the route to it, the run's; and one a row of another name stands for, with today's
# behaviour. tests/unit/test_aw_fleet_passthrough.py fails on an option passed through that defaults.yml, a role or a
# play sets for itself, by each road its scan reads, and on a road it cannot read or an include it does not follow.
LEFT_OUT = MappingProxyType(
    {
        "proxmox_vm_api_host": "is the host's connection: the host's address is a field of each host's host.yml",
        "proxmox_vm_api_port": "is the host's connection: the API's port is a field of each host's host.yml",
        "proxmox_vm_api_user": "is the host's connection: the token's user is a field of each host's host.yml",
        "proxmox_vm_api_token_id": "is the host's connection: the token's ID is a field of each host's host.yml",
        "proxmox_vm_ca_path": "is the host's connection: the API's certificate is checked against the anchor init keeps"
        " beside each host's host.yml",
        "proxmox_vm_validate_certs": "is no setting: the API's certificate is always checked",
        "proxmox_vm_api_timeout": "is the client's own: the engine's Proxmox client bounds each call itself",
        "proxmox_vm_tags": "is no setting: a VM is the fleet's by its pool and its record, never by a tag",
        "proxmox_vm_tags_1x": "is no setting: a VM is the fleet's by its pool and its record, never by a tag",
        "tailscale_enabled": "is set by the run from vpn, the setting that chooses the VPN",
        "tailscale_wanted": "is set by the run from vpn, the setting that chooses the VPN",
        "tailscale_off_setting": "is set by the run: the setting its messages name is vpn, which chooses the VPN",
        "proxmox_vm_template_name": "is chosen by the run: the template a VM is cloned from follows the template's"
        " settings and a golden template's key",
        "proxmox_template_build_vm": "is set by the role for itself, as it builds a golden template",
        "proxmox_template_known_hosts_dir": "is a folder of this computer's: a machine's host key is kept in the"
        " engine's own file of them",
        "github_projects_vm_repos": "is each machine's own: its record keeps the repositories deploy's --repos gave it",
        "tailscale_joined_tag": "is each machine's own: its record keeps whether its Tailscale had a login",
        "claude_code_host_flag": HOST_FLAG,
        "github_projects_host_flag": HOST_FLAG,
        "tailscale_host_flag": HOST_FLAG,
        "claude_code_ssh_via": SSH_VIA,
        "github_projects_ssh_via": SSH_VIA,
        "proxmox_vm_allow_untagged_delete": THROUGH.format(row="destroy_allow_unrecorded"),
    }
)


@dataclass(frozen=True)
class Option:
    """A typed option of a role that no row names: its name; its role; the entry point that first lists it, main
    first; the spec's type, choices and list elements; whether the spec marks it no_log; the spec's description, made
    one line; and refused, None where it is passed through, else why it is no setting, said after its name."""

    name: str
    role: str
    entry_point: str
    type: str
    choices: tuple[str, ...]
    elements: str | None
    no_log: bool
    description: str
    refused: str | None


@dataclass(frozen=True)
class _Table:
    options: tuple[Option, ...]
    by_name: Mapping[str, Option]
    refusals: Mapping[str, str]
    rows: Mapping[str, Row]


def _entry_points(role: str) -> dict[str, str]:
    """Each option of role's spec, with the first entry point that lists it, main first, as wiring.specs merges them."""
    import yaml

    path = wiring.ROOT / "roles" / role / "meta" / "argument_specs.yml"
    found: dict[str, str] = {}
    for entry_point, spec in sorted(
        yaml.safe_load(path.read_text())["argument_specs"].items(), key=lambda item: item[0] != MAIN
    ):
        for name in (spec or {}).get("options") or {}:
            found.setdefault(name, entry_point)
    return found


def _one_line(description: object) -> str:
    if isinstance(description, list):
        return " ".join(" ".join(str(each).split()) for each in description)
    return " ".join(str(description or "").split())


def _row_type(name: str, spec: Mapping[str, object]) -> str:
    """The row's type of a spec, or ValueError for a kind no row holds, a list of anything but strings and choices of
    anything but a string among them: a new kind of option needs its rule here before it is passed through. One TEXT
    names is a text, and its spec's type must be a string with no choices."""
    kind = spec.get("type", "str")
    if name in TEXT:
        if kind != "str" or spec.get("choices"):
            raise ValueError(f"{name} is text of several lines, which only a string with no choices holds")
        return "text"
    if kind == "list" and spec.get("elements") != "str":
        raise ValueError(f"{name} is a list of {spec.get('elements')}, which no row holds a value of")
    if kind not in ROW_TYPES:
        raise ValueError(f"{name} is of type {kind}, which no row holds a value of")
    if spec.get("choices") and kind != "str":
        raise ValueError(f"{name} has choices of type {kind}, which no row holds a value to")
    return ROW_TYPES[str(kind)]


@functools.cache
def _table() -> _Table:
    """Every option of the roles, sorted out once: the ones a row names, the ones passed through, the ones refused."""
    specs = wiring.specs()
    _defaults, feeds = wiring.wiring()
    rows = registry.rows()
    reached_by_a_row: dict[str, str] = {}
    for each in rows:
        for variable in wiring.set_by(each.name, feeds, specs):
            reached_by_a_row.setdefault(variable, each.name)
    the_vault_s = {
        variable for name in feeds if name.startswith(VAULT_NAME) for variable in wiring.set_by(name, feeds, specs)
    }
    per_machine = set(wiring.set_by(MACHINE_NAME, feeds, specs))
    row_names = {each.name for each in rows}
    options: list[Option] = []
    refusals: dict[str, str] = {}
    made: dict[str, Row] = {}
    for role in wiring.roles():
        entry_points = _entry_points(role)
        for name, spec in specs[role].items():
            if name in row_names:
                continue
            if name in reached_by_a_row and name not in LEFT_OUT:
                refusals[name] = THROUGH.format(row=reached_by_a_row[name])
                continue
            if spec.get("type") == "raw":
                refusals[name] = ANY_VALUE
                continue
            no_log = bool(spec.get("no_log"))
            if name in LEFT_OUT:
                refused: str | None = LEFT_OUT[name]
            elif no_log or name in the_vault_s:
                refused = SECRET
            elif name in per_machine:
                refused = PER_MACHINE
            elif name in BY_A_RUN:
                refused = SET_BY_A_RUN
            else:
                refused = None
            choices = tuple(str(each) for each in spec.get("choices") or ())
            elements = None if spec.get("elements") is None else str(spec["elements"])
            # The spec's own sentence for the option, what a person reads of it: no VM's description, and never sent.
            said = _one_line(spec.get("description"))
            option = Option(
                name, role, entry_points[name], str(spec.get("type", "str")), choices, elements, no_log, said, refused
            )
            options.append(option)
            if refused is None:
                kind = _row_type(name, spec)
                made[name] = Row(name, kind, choices, None, "global", "fleet", role, said)
            else:
                refusals[name] = refused
    return _Table(
        options=tuple(options),
        by_name=MappingProxyType({each.name: each for each in options}),
        refusals=MappingProxyType(refusals),
        rows=MappingProxyType(made),
    )


def options() -> tuple[Option, ...]:
    """Every typed option of the roles that no row names, refused or passed through, by role and in its spec's order:
    what the settings verb lists of a role beside its rows."""
    return _table().options


def option(name: str) -> Option:
    """The option of that name, refused or passed through; KeyError for a name that is no such option."""
    return _table().by_name[name]


def refusal(name: str) -> str | None:
    """Why a role's option is no setting, said after its name; None for one passed through, or a name that is no role's
    option a row does not have."""
    return _table().refusals.get(name)


def row(name: str) -> Row:
    """The row overrides.py reads an option passed through by: its spec's type and choices, no default (the role's own
    applies where it is set nowhere), scope global, home fleet, its role as its group and the spec's description.
    KeyError for any other name."""
    return _table().rows[name]


def typed(option: Option, text: str, *, environ: Mapping[str, str] | None = None) -> object:
    """text, as a person typed it, as the value option takes, held to its role's spec as overrides.typed holds text to
    a row; or overrides.Refused, which names the option and never says the value. One refused is refused for its
    reason."""
    from claude_vm.fleet import overrides

    if option.refused is not None:
        raise overrides.Refused(f"{option.name} {option.refused}")
    return overrides.typed(row(option.name), text, environ=environ)
