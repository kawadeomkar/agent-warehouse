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
config - this names none (the clean slate). The lines a VM's report says to run are aw's, each the printer's whole
line (schema/printer.py), where today's settings.py builds them with `just`; and the program Tailscale's line names
is aw's (status/lines.py's tailscale_report, which takes it). Status's own ssh options open no shared connection, where
today's did (OWN_SSH): the new engine opens a master in one place. The header that carries the token, which the Proxmox
client alone writes but for this, is made here for today's engine's reader, which aw status still reads through
(tests/unit/test_aw_pve_reads.py's HEADER_FOR_TODAY).

Beside today's settings, `load` gives under "pool" what aw status's reader of the fleet reads by
(lib/claude_vm/cli/status_pool.py): the host's pool, which init writes and without which nothing is read, what the
token may read beyond it (proxmox_audit_scope), the base template the settings name, the fleet's home, for this
computer's jobs, and the host a line names.

Of today's status it imports nothing: what it shares with it - the placeholder each VM's name replaces in what is
known of one VM, and the editors' rows - is held equal to today's by tests/unit/test_aw_status_inputs.py. The gh
login's marker on a VM is guest/states.py's and the ssh program the SSH runner's, each named once.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from claude_vm.editors.registry import editor_servers
from claude_vm.fleet import hosts, overrides, paths, registry, sealed, secrets, vaultkey
from claude_vm.guest import states
from claude_vm.guest.layer2 import claude_settings, remote_control
from claude_vm.hosts import model
from claude_vm.pve import template
from claude_vm.schema import printer
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
# The program every line a report says names, aw from the new engine (the owner's word 1).
PROGRAM: Final = "aw"
SYSTEM_ROOT: Final = "/"
SYSTEM_TIMEOUT: Final = 5
# Claude Code's method and Remote Control's unit, the role's options passed through, each its own default where none is
# set: layer 2's steps read the same (guest/layer2/remote_control.py).
INSTALL_METHOD: Final = "claude_code_install_method"
REMOTE_CONTROL_UNIT: Final = "claude_code_remote_control_service"
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


# Each command today's model and drawing name, by the key status/model.py's _line and tui/status.py read it under: its
# verb and values, the VM's name status/model.py's LINE_NAME where it names one, so that each VM's is put in its place.
LINES: Final = {
    "deploy": ("deploy", {"names": [PLACEHOLDER]}),
    "deploy_all": ("deploy", {}),
    "repair": ("deploy", {"names": [PLACEHOLDER], "only": "provision", "set": ["proxmox_vm_force_update=true"]}),
    "ssh_config": ("deploy", {"only": "ssh-config"}),
    "ssh_config_one": ("deploy", {"names": [PLACEHOLDER], "only": "ssh-config"}),
    "configure": ("deploy", {"names": [PLACEHOLDER], "only": "configure"}),
    "claude_code": ("deploy", {"names": [PLACEHOLDER], "tags": "claude_code"}),
    "tailscale": ("deploy", {"names": [PLACEHOLDER], "tags": "tailscale"}),
    "template": ("deploy", {"only": "template"}),
    "status": ("status", {}),
    "status_one": ("status", {"names": [PLACEHOLDER]}),
    "help_status": ("help", {"command": "status"}),
    "code_form": ("code", dict.fromkeys(("name", "project", "session"), printer.PLACEHOLDER)),
}


def lines(host: str) -> dict[str, str]:
    """LINES, each the printer's whole line, ending --host <host> where host is not "" (a fleet of more than one host);
    init's names the host by its own --host."""
    printed = {
        key: printer.line(verb, values, refused=False, host_flag=host if verb in ("deploy", "status", "code") else "")
        for key, (verb, values) in LINES.items()
    }
    printed["init"] = printer.line("init", {"host": host} if host else {}, refused=False, host_flag="")
    return printed


def _commands(name: str, host: str) -> dict[str, str]:
    """The lines a VM's report says to run, as today's roles make them, in aw's words: each the printer's whole line,
    ending --host <host> where the fleet has more than one host (host "" where it has one)."""

    def line(verb: str, values: Mapping[str, object]) -> str:
        return printer.line(verb, {"names": [name], **values}, refused=False, host_flag=host)

    return {
        "claude_login": line("login", {"only": "claude"}),
        "github_login": line("login", {"only": "github"}),
        "github_configure": line("deploy", {"tags": "github_projects"}),
        "tailscale_login": line("login", {"only": "tailscale"}),
        "tailscale_configure": line("deploy", {"tags": "tailscale"}),
        "reconfigure": line("deploy", {"only": "configure"}),
    }


def _vm(
    values: Mapping[str, overrides.Value],
    name: str,
    hostname: str,
    host: str,
    found: Mapping[tuple[str, str | None], Secret],
) -> dict:
    """What is known of the VM called name, whose tailnet name is hostname: today's VM block, made of the settings and
    whether each secret is set, never a secret itself; its lines end --host <host> where host is not ""."""
    value = {key: one.value for key, one in values.items()}
    lines = _commands(name, host)
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
    claude_method = value.get(INSTALL_METHOD) or remote_control.OPTIONS[INSTALL_METHOD]
    unit = value.get(REMOTE_CONTROL_UNIT) or remote_control.OPTIONS[REMOTE_CONTROL_UNIT]
    # Whether Remote Control is wanted, decided where layer 2 decides it.
    wanted = remote_control.remote_control_wanted(
        {
            "claude_code_remote_control": value["claude_code_remote_control"],
            claude_settings.SECRET: found.get((claude_settings.SECRET, None)),
        }
    )
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
            "method": str(claude_method),
            "api_key": api_key,
            "remote_control_wanted": wanted,
            "unit": str(unit),
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
            "reconfigure_command": lines["reconfigure"],
            # The program the line's own words name (status/lines.py's tailscale_report).
            "program": PROGRAM,
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
            "claude": {"unit": str(unit)},
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
    extra arguments, the two joined by a space): held as the SSH runner holds them for every other verb
    (runner._given: no option the product sets, such as HostKeyAlias, none that undoes what a connection holds, such
    as LocalCommand, each value one line), then -o and the pair as one shell-quoted word, in their order, since today's
    engine splits the line as a shell does. Refused in the runner's words, which name an option, never a value."""
    try:
        pairs = runner._given(options or {})
    except runner.Refused as refused:
        raise Refused(str(refused)) from None
    common = " ".join(f"-o {shlex.quote(f'{name}={value}')}" for name, value in pairs)
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
            # Every command the report and its drawing name, printed whole for this host (status/model.py's _line).
            "lines": lines(reached.host.name if reached.others else ""),
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
    host_named = name if reached.others else ""
    pool = values["proxmox_pool"].value
    if not pool:
        # As fleet/opened.py's connect refuses it: the fleet's VMs are its pool's members, so nothing can be read.
        raise Refused(
            said(
                "{name} names no pool (proxmox_pool), and the fleet's VMs are its members: {init} writes it",
                {"name": name, "init": Line("init", {"host": name})},
            )
        )
    per_vm = {}
    for machine in machines:
        try:
            own = overrides.resolve(fleet, name, run, machine=machine, environ=environ)
        except overrides.Refused as error:
            raise Refused(_words(error)) from None
        hostname = own["tailscale_hostname"].value or registry.tailnet_label(machine=machine)
        per_vm[machine] = {
            "vm": _vm(own, machine, str(hostname), host_named, found),
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
        "vm": _vm(values, PLACEHOLDER, registry.tailnet_label(machine=PLACEHOLDER), host_named, found),
        "reach": _reach(values, known_hosts, control_dir),
        "hop": _hop(reached, values, control_dir),
        "per_vm": per_vm,
        "inventory": {},
        "run": {"names": list(names), "only": opts.only},
        "password": password_of,
        "pool": _pool(fleet, name, host_named, values, known_hosts),
    }


def _pool(
    home: paths.Home, name: str, host: str, values: Mapping[str, overrides.Value], known_hosts: str
) -> dict[str, object]:
    """What aw status's reader of the fleet reads by (lib/claude_vm/cli/status_pool.py): the host's pool, what the token
    may read beyond it, the base template the settings name with what a build of it makes, this computer's known-hosts
    file and its folder of jobs, and the host a line names ("" where the fleet has one host)."""
    value = {key: one.value for key, one in values.items()}
    return {
        "host": name,
        "host_flag": host,
        "pool": str(value["proxmox_pool"]),
        "scope": str(value["proxmox_audit_scope"]),
        "base": template.Base.of(value, pool=str(value["proxmox_pool"]), host_flag=host),
        "want": template.wanted(value),
        "custom": bool(value["proxmox_template_custom"]),
        "known_hosts": known_hosts,
        "jobs": str(paths.jobs_dir(home)),
    }
