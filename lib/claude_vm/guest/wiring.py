"""The system play's variables for one machine, a pure function of the settings (docs/architecture/engine.md §16.10).

Layer 1 hands the system play one variables file (guest/variables.py) and an inventory of one host, and nothing else:
no group_vars, no host_vars, and no template of today's wiring (inventory/group_vars/all/defaults.yml) is read. So
every variable the play's roles read that a person's settings decide is here, in `TABLE`: for each role of the play, in
its order, each variable of its meta/argument_specs.yml, every entry point's (for `tailscale`, whose other entries are
today's play's, its entry `system`'s alone: `ENTRY`), with where its value comes from, written by hand as plain data, a
`Source` of one of four kinds:

- `setting`: the value of a row of the settings table (fleet/registry.py), by the row's name: `common_user`,
  `vscode_server_user` and `dev_tools_user` are `vm_user`'s; `common_user_ssh_public_keys` is `vm_ssh_public_keys`',
  `common_extra_packages` `vm_extra_packages`' and `vscode_server_enabled` `vscode_remote`'s; and each of the six of
  `dev_tools` that is a row of its own name is that row's;
- `chosen`: whether a row holds one value: `tailscale_wanted`, whether `vpn` is `tailscale`, as today's wiring makes it
  from the VPN's key (inventory/group_vars/all/defaults.yml), so that Tailscale's part installs the package only where
  it is the VPN;
- `machine`: the name a person chose for the machine: `common_hostname`, whose role default reads `inventory_hostname`,
  which layer 1's inventory makes the machine's identity, never its name;
- `option`: a role's option passed through by its own name (fleet/passthrough.py), handed only where the settings hold
  it, so that where none is set the role's own default applies where the role runs, those it works out from the
  machine's facts among them (`dev_tools_docker_manage_service`, `dev_tools_deb_arch`).

`INTERNALS` names the role variables handed a fixed value, each with why: `common_user_password`, empty (review R2,
L-3). Today it is made from the vault's `vault_vm_password` and matters only where `common_user_passwordless_sudo` is
false, which guest/system.py refuses until F24 hands the password to Ansible's own become-password file on a pipe; no
secret is ever in the variables file (guest/variables.py). F24 changes this line. `PLAY` is the play's own variable,
which its opening tasks read under any part: `configure_wait_for_cloud_init`, its row's value. `NAMES` is every name of
the three, the one list of the names the system play's variables may hold: guest/system.py's `variables` writes no other
and layer1.run's hold of the file takes no other, since an extra variable would outrank the play's own check, a register
or a role's internal of any other name for every task.

`role_variables(settings, target, parts, profile={})` returns the play's variables for one machine, the document
guest/system.py's `variables` writes: `settings` is fleet/overrides.py's `resolve` for the machine's run (each name's
`Value`, with the layer that set it); `target` the machine's name, one VM name as layer 1 holds it; `parts` the play's
parts the run asks for (guest/system.py's `PARTS`), whose roles' variables it gives, after the play's own; and
`profile` what a kind of machine lays under a person's settings (J2's, empty for a VM): by a setting's name or an
option's, each laid only where the person set nothing, a row still at its default and an option set nowhere. Every value
is a copy. Refused, naming it, before anything is made: a part that is none of the play's, or given twice; a target
that is no VM name; a profile's name that is no setting or option of the table; and a name of the settings that is a
role variable made here and no row of its own name - `common_user`, made from `vm_user`, `common_hostname`, the
machine's name, or the internal `common_user_password` - which a run whose overrides set it never hands the play.
tests/unit/test_aw_guest_wiring.py holds the table to the roles' specs and to today's wiring, and its output, setting by
setting, to what Ansible gives the same roles from today's inventory.

Standard library and the patterns: the settings are read by their attributes, never by importing fleet/.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from claude_vm.schema import patterns

if TYPE_CHECKING:
    from claude_vm.fleet.overrides import Value

# Where a role variable's value comes from: a row of the settings table, whether a row holds one value, the machine's
# own name, or the role's option of the same name, passed through.
SETTING = "setting"
CHOSEN = "chosen"
MACHINE = "machine"
OPTION = "option"
KINDS = (SETTING, CHOSEN, MACHINE, OPTION)
# The layer of a row's value that no file and no run set: the table's default (fleet/overrides.py's DEFAULT), the one a
# profile is laid over.
DEFAULT = "default"


@dataclass(frozen=True)
class Source:
    """Where one variable's value comes from: its kind, one of KINDS, for a setting or a choice the row's name, and for
    a choice the value it is true at."""

    kind: str
    setting: str | None = None
    chosen: str | None = None


@dataclass(frozen=True)
class Internal:
    """A role variable handed a fixed value: the role whose it is, the value, and why."""

    role: str
    value: object
    why: str


def _row(name: str) -> Source:
    return Source(SETTING, name)


OWN = Source(OPTION)
NAMED = Source(MACHINE)

# Each role of the system play, in its order (guest/system.py's PARTS), with each variable of its
# meta/argument_specs.yml and where its value comes from.
TABLE: Mapping[str, Mapping[str, Source]] = MappingProxyType(
    {
        "common": MappingProxyType(
            {
                "common_user": _row("vm_user"),
                "common_user_shell": OWN,
                "common_user_groups": OWN,
                "common_user_passwordless_sudo": OWN,
                "common_user_groups_revoked": OWN,
                "common_user_ssh_public_keys": _row("vm_ssh_public_keys"),
                "common_timezone": OWN,
                "common_manage_hostname": OWN,
                "common_hostname": NAMED,
                "common_harden_ssh": OWN,
                "common_ssh_path_dirs": OWN,
                "common_tmux_settings": OWN,
                "common_apt_upgrade": OWN,
                "common_apt_cache_valid_time": OWN,
                "common_apt_lock_timeout": OWN,
                "common_apt_keep_downloads": OWN,
                "common_container_virt_types": OWN,
                "common_packages": OWN,
                "common_extra_packages": _row("vm_extra_packages"),
                "common_enabled_services": OWN,
                "common_masked_units": OWN,
            }
        ),
        "vscode_server": MappingProxyType(
            {
                "vscode_server_enabled": _row("vscode_remote"),
                "vscode_server_user": _row("vm_user"),
                "vscode_server_client_alive_interval": OWN,
                "vscode_server_client_alive_count_max": OWN,
                "vscode_server_inotify_watches": OWN,
                "vscode_server_container_virt_types": OWN,
            }
        ),
        "dev_tools": MappingProxyType(
            {
                "dev_tools_user": _row("vm_user"),
                "dev_tools_install_node": _row("dev_tools_install_node"),
                "dev_tools_install_docker": _row("dev_tools_install_docker"),
                "dev_tools_install_gh": _row("dev_tools_install_gh"),
                "dev_tools_install_uv": _row("dev_tools_install_uv"),
                "dev_tools_install_rust": _row("dev_tools_install_rust"),
                "dev_tools_apt_packages": OWN,
                "dev_tools_node_major": _row("dev_tools_node_major"),
                "dev_tools_npm_global_packages": OWN,
                "dev_tools_docker_packages": OWN,
                "dev_tools_container_virt_types": OWN,
                "dev_tools_docker_manage_service": OWN,
                "dev_tools_deb_arch": OWN,
                "dev_tools_uv_version": OWN,
                "dev_tools_uv_arch_map": OWN,
                "dev_tools_uv_sha256": OWN,
                "dev_tools_uv_install_dir": OWN,
                "dev_tools_rust_toolchain": OWN,
                "dev_tools_rust_components": OWN,
            }
        ),
        # Its entry system's alone (ENTRY): the package, where Tailscale is the VPN. The join is layer 2's.
        "tailscale": MappingProxyType(
            {
                "tailscale_wanted": Source(CHOSEN, "vpn", "tailscale"),
                "tailscale_upload_logs": _row("tailscale_upload_logs"),
                "tailscale_daemon_flags": _row("tailscale_daemon_flags"),
                "tailscale_marker_dir": OWN,
            }
        ),
    }
)
# The entry point of a role whose variables alone the table holds, where the play imports that entry and the role's
# others are today's play's.
ENTRY: Mapping[str, str] = MappingProxyType({"tailscale": "system"})
# The parts of the play, each a role of it, in its order.
PARTS = tuple(TABLE)

# The role variables handed a fixed value, each with why.
INTERNALS: Mapping[str, Internal] = MappingProxyType(
    {
        "common_user_password": Internal(
            "common",
            "",
            "the password sudo asks for with passwordless sudo off, which the engine refuses until F24 hands it to"
            " Ansible's become-password file on a pipe: no secret is ever in the play's variables",
        ),
    }
)

# The play's own variables, which its opening tasks read under any part.
PLAY: Mapping[str, Source] = MappingProxyType({"configure_wait_for_cloud_init": _row("configure_wait_for_cloud_init")})

# Every name the system play's variables may hold, and no other: the play's own, each role variable of the table and
# each internal, the one list, written here once. guest/system.py's `variables` and layer1.run's hold refuse any other
# name: an extra variable outranks a play's vars, a task's vars, a set_fact and a register for every task, so a name of
# the play's own check, of a register or of a role's internals would turn them off.
NAMES: frozenset[str] = frozenset({*PLAY, *INTERNALS, *(name for role in TABLE.values() for name in role)})


class Refused(ValueError):
    """Settings, a machine, parts or a profile the play's variables are not made from, said by what is wrong: nothing
    was made."""


def made_here(name: str) -> str | None:
    """Why a name the settings may not hold is made here, never set, said after its name; None for one they may: a row
    of its own name, an option of a role passed through, or a name no role of the play reads."""
    for role in TABLE.values():
        source = role.get(name)
        if source is None:
            continue
        if source.kind == MACHINE:
            return "is the machine's own name, which the play is given as the machine's: it is never set"
        if source.kind == SETTING and source.setting != name:
            return f"is made from {source.setting}, a setting of its own: set {source.setting} instead"
        if source.kind == CHOSEN:
            return f"is made from {source.setting}, a setting of its own: set {source.setting} instead"
        return None
    if name in INTERNALS:
        return f"is the engine's own, never set: {INTERNALS[name].why}"
    return None


def _settings(settings: object) -> Mapping[str, Value]:
    """The settings held: a mapping of names to values, each with the layer that set it, and none a variable made
    here."""
    if not isinstance(settings, Mapping):
        raise Refused("the settings are a mapping of names to their values, as fleet/overrides.py's resolve gives them")
    for name, value in settings.items():
        why = made_here(name) if isinstance(name, str) else None
        if why is not None:
            raise Refused(f"{name} {why}")
        if not (hasattr(value, "value") and isinstance(getattr(value, "layer", None), str)):
            raise Refused(
                "each setting's value comes with the layer that set it, as fleet/overrides.py's resolve gives one"
            )
    return settings


def _target(target: object) -> str:
    """The machine's name a person chose: one VM name, never several."""
    if not isinstance(target, str) or "," in target or not patterns.full("vm_name", target):
        raise Refused("a machine's name is one VM name: letters, digits, dots, underscores and hyphens")
    return target


def _parts(parts: object) -> tuple[str, ...]:
    """The parts asked for, each a role of the play, once, in the play's order."""
    if isinstance(parts, (str, bytes)) or not isinstance(parts, Sequence):
        raise Refused("the parts asked for are a list of the play's parts")
    if not all(isinstance(part, str) and part in TABLE for part in parts) or len(set(parts)) != len(parts):
        raise Refused(f"each part asked for is one of the play's, once: {', '.join(PARTS)}")
    return tuple(part for part in PARTS if part in parts)


def _laid() -> frozenset[str]:
    """The names a profile may lay: each setting the play's variables are made from, and each option of its roles."""
    sources = [*PLAY.values(), *(source for role in TABLE.values() for source in role.values())]
    options = {name for role in TABLE.values() for name, source in role.items() if source.kind == OPTION}
    return frozenset({source.setting for source in sources if source.kind in (SETTING, CHOSEN)} | options)


def _profile(profile: object) -> Mapping[str, object]:
    """A profile held: a mapping of names a profile may lay to their values."""
    if not isinstance(profile, Mapping):
        raise Refused("a profile is a mapping of settings and options to their values")
    allowed = _laid()
    if not all(isinstance(name, str) and name in allowed for name in profile):
        raise Refused(
            "a profile lays a setting the play's variables are made from, or an option of its roles: no other"
        )
    return profile


def role_variables(
    settings: Mapping[str, Value],
    target: str,
    parts: Sequence[str],
    profile: Mapping[str, object] = MappingProxyType({}),
) -> dict[str, object]:
    """The play's variables for the machine named target: its own, then each variable of the roles of parts, as TABLE
    and INTERNALS make them from settings (fleet/overrides.py's resolve), profile laid where the person set nothing.
    Each value a copy; Refused, before anything is made, as the module says."""
    held = _settings(settings)
    name = _target(target)
    roles = _parts(parts)
    laid = _profile(profile)

    def setting(row: str, variable: str) -> object:
        if row not in held:
            raise Refused(
                f"the settings hold no value of {row}, which {variable} is made from: give fleet/overrides.py's resolve"
            )
        if held[row].layer == DEFAULT and row in laid:
            return laid[row]
        return held[row].value

    made: dict[str, object] = {}
    for variable, source in PLAY.items():
        made[variable] = setting(str(source.setting), variable)
    for role in roles:
        for variable, source in TABLE[role].items():
            if source.kind == SETTING:
                made[variable] = setting(str(source.setting), variable)
            elif source.kind == CHOSEN:
                made[variable] = setting(str(source.setting), variable) == source.chosen
            elif source.kind == MACHINE:
                made[variable] = name
            elif variable in held:
                made[variable] = held[variable].value
            elif variable in laid:
                made[variable] = laid[variable]
        for variable, internal in INTERNALS.items():
            if internal.role == role:
                made[variable] = internal.value
    return copy.deepcopy(made)
