"""aw status's settings, worked out in this process from the fleet's files (docs/architecture/engine.md §16.3): `load`
gives today's status engine (lib/claude_vm/status/engine.py) the one dict today's lib/claude_vm/status/settings.py's
`load` gives it, key for key, made from the registry's defaults under the overrides - the fleet's settings.yml, the
host's own file and client.yml - with the run's `--set` values on top of them all, and the host's host.yml, its anchor
and the vault's secrets. No Ansible is asked, nothing is kept: every value is worked out again at each run.

What `--set` gives is typed by fleet/overrides.py's `run_values` before any file is read, so a name that is no
setting's, a secret's row and a value of the wrong type are each refused first, never said back; once the vault is
open, a value that is one of its secrets is refused too. Then the host is the one fleet/hosts.py's `reach` picks: the
one `--host` names, the one the VMs named are on as their aliases record, client.yml's current host, the fleet's one.
Its token's secret is read from the vault at each run and goes into the API's one header; the API is reached at the
address and port host.yml gives, its certificate checked against the anchor the host keeps, else against this
computer's certificates (tls_ca_file's file where it names one), and always checked.

Where today's settings named something of 1.x's - a tag a VM of 1.x carried, its markers on a VM, its managed SSH
config - this names none (the clean slate). And today's settings.py builds the lines a VM's report says with `just`:
so do these, until the card that fills aw status's handler (G16) makes them aw's. Status's own ssh options open no
shared connection, where today's did (OWN_SSH): the new engine opens a master in one place. The header that carries
the token, which the Proxmox client alone writes but for this, is made here for today's engine's reader, until G16
(tests/unit/test_aw_pve_reads.py's HEADER_FOR_TODAY).

Of today's status it imports nothing: what it shares with it - the placeholder each VM's name replaces in what is
known of one VM, and the editors' rows - is held equal to today's by tests/unit/test_aw_status_inputs.py. The gh
login's marker on a VM is guest/states.py's and the ssh program the SSH runner's, each named once.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from claude_vm.editors.registry import editor_servers
from claude_vm.fleet import hosts, overrides, paths, registry, sealed, secrets, vaultkey
from claude_vm.guest import states
from claude_vm.hosts import model
from claude_vm.pve import template
from claude_vm.schema.printer import said
from claude_vm.schema.words import Line
from claude_vm.secret import Secret
from claude_vm.ssh import runner

# The name what is known of one VM is worked out for, which today's engine replaces with each VM's own (for_vm): today's
# status/settings.py's PLACEHOLDER.
PLACEHOLDER: Final = "status_placeholder.vm"

# The editors, by their key, as today's inventory defaults list them (claude_editors): each one's name, for the line
# under ACCESS, and the window its server shows on a VM (editors/registry.py's editor_servers).
EDITORS: Final[Mapping[str, Mapping[str, Any]]] = {
    "vscode": {
        "name": "VS Code",
        "family": "vscode",
        "window_tasks": True,
        "claude_terminal": "started",
        "cli_candidates": ["code"],
        "server": {"argv0_contains": ".vscode-server", "argv_has": ["--type=extensionHost"]},
    },
    "zed": {
        "name": "Zed",
        "family": "zed",
        "window_tasks": False,
        "claude_terminal": "listed",
        "cli_candidates": ["zeditor", "zed"],
        "version_re": "^Zed ",
        "server": {"argv0_name_prefix": "zed-remote-server", "argv1": "run"},
    },
}

# What today's settings give every run alike, from the inventory defaults and the roles' own: how long one request to
# the API may take; how a VM is reached and made root on it; what the probe is asked, and where on a VM it looks.
API_TIMEOUT: Final = 10.0
PYTHON: Final = "/usr/bin/python3"
BECOME_EXE: Final = "/usr/bin/sudo"
# Status's own ssh options, first on ssh's line, as today's status_ssh_args makes them: the host key held to what this
# computer knows, never taken or updated. Not today's shared connection (ControlMaster=auto, ControlPersist): only the
# SSH runner's ensure opens a master (ssh/connections.py), so a probe rides a connection that is open and opens none.
OWN_SSH: Final = "-o StrictHostKeyChecking=yes -o UpdateHostKeys=no"
ASKED_LIMIT: Final = 25
SYSTEM_ROOT: Final = "/"
SYSTEM_TIMEOUT: Final = 5
REMOTE_CONTROL_UNIT: Final = "claude-remote-control"
INSTALL_METHOD: Final = "native"
# The gh login's marker as today's settings give the probe it, from the VM user's home.
GH_MARKER: Final = "./" + states.GITHUB_MARKER.removeprefix("~/")
TAILSCALE_DIR: Final = "/var/lib/agent-warehouse"
TAILSCALE_FOREIGN: Final = (
    "/usr/sbin/tailscaled",
    "/usr/local/sbin/tailscaled",
    "/usr/local/bin/tailscaled",
    "/snap/bin/tailscale",
)
TAILSCALE_ADMIN: Final = "https://login.tailscale.com/admin/machines"
KNOWN_HOSTS: Final = "agent-warehouse.known_hosts"
# The machines this computer reaches by a name of its own, which no VM may be named after: the run's host and the
# fleet's other hosts are added to it.
LOCALHOST: Final = "localhost"


class Refused(Exception):
    """aw status cannot read as the fleet's files say: one sentence, which never says a value given or a secret."""

    def __init__(self, sentence: str, notes: Sequence[model.Note] = ()) -> None:
        self.sentence = sentence
        self.notes = tuple(notes)
        super().__init__(sentence)


@dataclass(frozen=True)
class Opts:
    """The run's words, as today's engine reads its options: the VMs named, comma-joined; the sections; --json; and the
    folder apt's answers are kept in, "" for none. set is the run's --set words as typed, which load types. The rest are
    today's options that aw status has no word for, each as tools/status has it given none: no child per host (emit), no
    vault password file, no -e, no --private-key and no -i."""

    names: str = ""
    only: str = ""
    json: bool = False
    set: tuple[str, ...] = ()
    cache: str = ""
    emit: bool = False
    vault_password_file: str = ""
    extra: tuple[str, ...] = ()
    private_key: str = ""
    inventory: tuple[str, ...] = ()


def _words(error: BaseException) -> str:
    """A refusal of fleet/'s, made this module's sentence: no "error: " before it."""
    return str(error).removeprefix("error: ")


def _names(opts: Opts) -> tuple[str, ...]:
    """The VMs named, as Proxmox takes a name: my_vm is my-vm, as hosts/model.py's Request takes them."""
    return tuple(name.replace("_", "-") for name in opts.names.split(",") if name)


# ---------------------------------------------------------------------------------------------- the secrets ----


def _secrets(opened: Mapping[str, Secret]) -> dict[tuple[str, str | None], Secret]:
    """The vault's map, by each entry's row and owner: (name, None) for the fleet's, (name, host or machine) for one's
    own. An entry no row names is left out, as engine/fleet.py's reader leaves it."""
    found = {}
    for entry, value in opened.items():
        try:
            row, owner = secrets.parsed(entry)
        except secrets.Refused:
            continue
        found[(row.name, owner)] = value
    return found


def _set(found: Mapping[tuple[str, str | None], Secret], name: str) -> bool:
    """Whether the vault holds the fleet's secret name: never what it holds."""
    return (name, None) in found


# ------------------------------------------------------------------------------------- what one VM is known as ----


def _commands(name: str, host_flag: str) -> dict[str, str]:
    """The lines a VM's report says to run, as today's roles make them (just, until G16)."""
    return {
        "claude_login": f"just login {name} --only claude{host_flag}",
        "github_login": f"just login {name} --only github{host_flag}",
        "github_configure": f"just deploy {name} --tags github_projects{host_flag}",
        "tailscale_login": f"just login {name} --only tailscale{host_flag}",
        "tailscale_configure": f"just deploy {name} --tags tailscale{host_flag}",
    }


def _vm(
    values: Mapping[str, overrides.Value],
    name: str,
    hostname: str,
    host_flag: str,
    found: Mapping[tuple[str, str | None], Secret],
) -> dict:
    """What is known of the VM called name, whose tailnet name is hostname: today's VM block, made of the settings and
    whether each secret is set, never a secret itself."""
    value = {key: one.value for key, one in values.items()}
    lines = _commands(name, host_flag)
    api_key = _set(found, "anthropic_api_key")
    token_set = _set(found, "github_token")
    key = found.get(("tailscale_auth_key", None))
    vpn = str(value["vpn"])
    tailscale = vpn == "tailscale"
    key_wanted = value["tailscale_auth_method"] != "web" and key is not None
    tags = list(value["tailscale_advertise_tags"])
    # An OAuth client's secret needs tags, without which tailscale refuses it on the VM: read for its kind alone.
    client = key is not None and key.reveal().startswith("tskey-client-")
    method = str(value["github_auth_method"])
    projects = value["vm_projects_dir"] or ""
    user = value["vm_user"]
    return {
        "drift": {
            "cores": int(value["vm_cores"]),
            "memory": int(value["vm_memory_mb"]),
            "balloon": int(value["vm_memory_min_mb"]),
            "onboot": True,
            "disk": value["vm_disk_size"],
            "ciuser": user,
            "sshkeys": list(value["vm_ssh_public_keys"]),
            "ipconfig0": value["vm_ipconfig"],
            "nameserver": list(value["vm_nameservers"]),
            "searchdomain": value["vm_searchdomain"],
        },
        "sized_tag": f"{value['vm_label']}-sized",
        "claude": {
            "method": INSTALL_METHOD,
            "api_key": api_key,
            "remote_control_wanted": value["claude_code_remote_control"] is True and not api_key,
            "unit": REMOTE_CONTROL_UNIT,
            "login_command": lines["claude_login"],
        },
        "github": {
            "user": user,
            "auth": value["github_auth"] is True,
            "auth_method": method,
            "token_wanted": method == "token" or (method == "auto" and token_set),
            "token_set": token_set,
            "configure_command": lines["github_configure"],
            "login_command": lines["github_login"],
            "workflow_asked": "workflow" in value["github_login_scopes"],
        },
        "tailscale": {
            "wanted": tailscale,
            "hostname": hostname,
            "ssh": value["tailscale_ssh"] is True,
            "accept_routes": value["tailscale_accept_routes"] is True,
            "accept_dns": value["tailscale_accept_dns"] is True,
            "advertise_tags": tags,
            "login_server": value["tailscale_login_server"],
            "auth_method": value["tailscale_auth_method"],
            "key_wanted": key_wanted,
            "key_refused_locally": key_wanted and client and not tags,
            "joined_tag": f"{value['vm_label']}-tailscale",
            "admin_page": "your control server" if value["tailscale_login_server"] else TAILSCALE_ADMIN,
            "configure_command": lines["tailscale_configure"],
            "login_command": lines["tailscale_login"],
        },
        "vpn": vpn,
        "projects_dir": projects,
        "asked": {
            "user": user,
            "limit": ASKED_LIMIT,
            "system": {
                "root": SYSTEM_ROOT,
                "upgrades": True,
                "timeout": SYSTEM_TIMEOUT,
                "editors": editor_servers(EDITORS),
            },
            "claude": {"unit": REMOTE_CONTROL_UNIT},
            "github": {"marker": GH_MARKER, "marker_1x": ""},
            "work": {"dir": projects},
            "tailscale": {
                "wanted": tailscale,
                "foreign": list(TAILSCALE_FOREIGN),
                "marker": f"{TAILSCALE_DIR}/tailscale-installed",
                "marker_1x": "",
                "joined": f"{TAILSCALE_DIR}/tailscale-joined",
                "joined_1x": "",
            },
        },
    }


def _given(options: object) -> str:
    """The person's own ssh options for the VMs, as the words ssh is given after status's own (today's ssh common and
    extra arguments, the two joined by a space): -o Name=value each, in their order."""
    pairs = options.items() if isinstance(options, Mapping) else ()
    common = " ".join(f"-o {name}={value}" for name, value in pairs)
    return f"{common} "


def _reach(values: Mapping[str, overrides.Value], known_hosts: str, control_dir: str) -> dict:
    """How a VM is reached, but for its address: today's REACH, over ssh as the VM user, status's own options first."""
    given = _given(values["vm_ssh_options"].value)
    return {
        "connection": "ssh",
        "user": values["vm_user"].value,
        "port": "",
        "key": values["claude_ssh_identity_file"].value or "",
        "python": PYTHON,
        "ssh_args": f"{OWN_SSH} {given}",
        "known_hosts": known_hosts,
        "ssh_executable": values["ssh_executable"].value or runner.SSH_PROGRAM,
        "control_dir": control_dir,
        "become": True,
        "become_exe": BECOME_EXE,
        "own": OWN_SSH,
        "given": given,
    }


# ------------------------------------------------------------------------------------------------ the fleet ----


def _pattern(tags: Sequence[str], suffix: str = "") -> str:
    """A VM's tags hold one of tags (with suffix, a fixed word that needs no escape), whole, as Proxmox joins them (;
    or ,), in any case."""
    return rf"(?i)(^|[;,])({'|'.join(re.escape(tag) for tag in tags)}){suffix}([;,]|\Z)"


def _anchor_file(layout: hosts.Layout, name: str) -> str:
    """The file the host's anchor was read from: proxmox-trust.pem, or the write staged beside it where only that is
    there (hosts.Layout.anchor's rule)."""
    folder = layout.hosts_dir / name
    kept = folder / hosts.ANCHOR
    return str(kept if kept.exists() else folder / hosts.STAGED)


def _fleet(
    layout: hosts.Layout,
    reached: hosts.Reached,
    values: Mapping[str, overrides.Value],
    token: Secret,
    host_flag: str,
    known_hosts: str,
) -> dict:
    """What the fleet is read with, and the report's own settings: today's FLEET and API blocks."""
    value = {key: one.value for key, one in values.items()}
    name, fields = reached.host.name, reached.fields
    label = str(value["vm_label"])
    owned = [label]
    custom = str(value["proxmox_template_custom"] or "")
    base = custom or str(value["proxmox_template_name"])
    editor = str(value["editor"])
    vpn = str(value["vpn"])
    at = f"[{fields.address}]" if ":" in fields.address else fields.address
    anchor = _anchor_file(layout, name) if reached.anchor is not None else ""
    aliases = paths.user_home(layout.environ).joinpath(*hosts.ALIASES)
    return {
        "tag_pattern": _pattern(owned),
        "tailscale_tag_pattern": _pattern(owned, "-tailscale"),
        "template_name": base,
        "storage": value["proxmox_storage"],
        "nic": value["proxmox_nic"],
        "ssh_config": value["claude_ssh_config"] is True,
        "ssh_config_file": str(aliases),
        "ssh_config_legacy": "",
        "hosts": list(dict.fromkeys([LOCALHOST, name, *(other.name for other in reached.others)])),
        "template_wanted": template.wanted(value),
        "forks": int(value["run_forks"]),
        "route": value["vm_ssh_route"],
        "url": f"https://{at}:{fields.api_port}/api2/json",
        "headers": {"Authorization": f"PVEAPIToken={fields.token_user}!{fields.token_id}={token.reveal()}"},
        # The certificate is always checked: against the host's anchor where it keeps one, else this computer's own.
        "validate": True,
        "ca_path": anchor or str(value["tls_ca_file"] or ""),
        "trust_file": anchor,
        "timeout": API_TIMEOUT,
        "settings": {
            "tag": label,
            "tags_owned": owned,
            "api_user": fields.token_user,
            "template_name": base,
            "template_custom": bool(custom),
            "template_rebuild": value["proxmox_template_rebuild"] is True,
            "storage": value["proxmox_storage"],
            "nic": value["proxmox_nic"],
            "pool": value["proxmox_pool"] or "",
            "tailscale_enabled": vpn == "tailscale",
            "vpn": vpn,
            "tailscale_off": f"vpn is {vpn}",
            "vm_user": value["vm_user"],
            "known_hosts": known_hosts,
            "discovery": True,
            "api_checked": True,
            "editor": editor,
            "editor_name": str(EDITORS.get(editor, {}).get("name") or editor),
            "editor_names": {key: str(row.get("name", "")) for key, row in EDITORS.items()},
            "host_flag": host_flag,
        },
    }


def _hop(reached: hosts.Reached, values: Mapping[str, overrides.Value], control_dir: str) -> dict:
    """The Proxmox host's own settings, for the hop through it where the VMs are reached through it: host.yml's address,
    account, port and jump, and this computer's key and ssh for it."""
    fields = reached.fields
    return {
        "name": reached.host.name,
        "host": fields.address,
        "user": fields.ssh_user,
        "port": fields.ssh_port,
        "key": values["ssh_identity_file"].value or "",
        "ssh_executable": values["ssh_executable"].value or runner.SSH_PROGRAM,
        "control_dir": control_dir,
        "jump": fields.jump,
    }


def _machines(home: paths.Home, host: str, environ: Mapping[str, str]) -> list[str]:
    """The machines of host that a file sets a setting of their own for: the fleet's settings.yml's and client.yml's,
    each kept under <host>/<machine>."""
    layers = overrides.read(home, host, environ=environ)
    keys = [*layers.fleet.machines, *layers.client.machines]
    return sorted({key.split("/", 1)[1] for key in keys if key.startswith(f"{host}/")})


# ------------------------------------------------------------------------------------------------- loading ----


def _run_values(opts: Opts, known: Sequence[Secret], environ: Mapping[str, str]) -> dict[str, object]:
    try:
        return overrides.run_values(opts.set, secrets=known, environ=environ)
    except overrides.Refused as error:
        raise Refused(_words(error)) from None


def load(
    fleet: paths.Home,
    host: str | None,
    opts: Opts,
    *,
    environ: Mapping[str, str],
    noted: Callable[[model.Note], None] | None = None,
) -> dict:
    """{"fleet", "vm", "reach", "hop", "per_vm", "inventory", "run", "password"}, as today's settings.load gives them,
    for a run against host (None: the one fleet/hosts.py's reach picks) of the fleet at fleet, with opts' --set values
    on top; each note said on choosing the host handed to noted. Refused, in one sentence, before anything is sent: a
    --set word refused, the fleet's files not what they may be, no host, the vault that does not open or holds no token
    for the host."""
    # Typed before any file is read: a refused word is said first, and no value of it is said.
    run = _run_values(opts, (), environ)
    layout = hosts.Layout(fleet, environ)
    names = _names(opts)
    try:
        reached = hosts.reach(layout, model.Request(host=host or "", names=names, read_only=True))
    except hosts.Refused as refused:
        raise Refused(_words(refused), refused.notes) from None
    name = reached.host.name
    for note in reached.notes if noted else ():
        noted(note)
    try:
        values = overrides.resolve(fleet, name, run, environ=environ)
        machines = _machines(fleet, name, environ)
        key = vaultkey.open(
            fleet,
            key_file=values["vault_key_file"].value,
            key_command=values["vault_key_command"].value,
            environ=environ,
        )
        opened = sealed.read(fleet, key) or {}
        control_dir = str(paths.control_dir(environ))
        known_hosts = str(paths.user_home(environ).joinpath(*hosts.ALIASES).parent / KNOWN_HOSTS)
    except (overrides.Refused, paths.Refused, sealed.Refused, hosts.Refused) as error:
        raise Refused(_words(error)) from None
    # Once the vault is open: a value given for this run is never one of its secrets.
    if opts.set:
        run = _run_values(opts, list(opened.values()), environ)
    found = _secrets(opened)
    token = found.get(("token", name))
    if token is None:
        raise Refused(
            said(
                "the vault holds no token's secret for {name}, so nothing of it can be read: {vault} puts it",
                {"name": name, "vault": Line("vault", {"name": "token", "host": name})},
            )
        )
    host_flag = f" --host {name}" if reached.others else ""
    per_vm = {}
    for machine in machines:
        try:
            own = overrides.resolve(fleet, name, run, machine=machine, environ=environ)
        except overrides.Refused as error:
            raise Refused(_words(error)) from None
        hostname = own["tailscale_hostname"].value or registry.tailnet_label(machine=machine)
        per_vm[machine] = {
            "vm": _vm(own, machine, str(hostname), host_flag, found),
            "reach": _reach(own, known_hosts, control_dir),
        }
    password = found.get(("vm_password", None))
    passwords = {one: found[("become_password", one)] for one in machines if ("become_password", one) in found}

    def password_of(machine: str = "") -> str:
        """The VM user's password on machine, for a sudo that asks for one: read only then."""
        held = passwords.get(machine, password)
        return "" if held is None else held.reveal()

    return {
        "fleet": _fleet(layout, reached, values, token, host_flag, known_hosts),
        "vm": _vm(values, PLACEHOLDER, registry.tailnet_label(machine=PLACEHOLDER), host_flag, found),
        "reach": _reach(values, known_hosts, control_dir),
        "hop": _hop(reached, values, control_dir),
        "per_vm": per_vm,
        "inventory": {},
        "run": {"names": list(names), "only": opts.only},
        "password": password_of,
    }
