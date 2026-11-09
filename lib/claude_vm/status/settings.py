"""What just status needs of the settings: asked of Ansible once, and kept until a file they are made from changes.

Ansible's own VariableManager and templar, in this process, as tools/settings asks them, the first time and whenever
inventory/, ansible.cfg or a role's defaults or vars have changed; never by a second reading of its precedence here.
The answer is kept in .cache/status/settings.json (0600, git-ignored, refused by tests/check_no_local_files.sh), keyed
by the name, size and time of every file it is made from, so a later run starts without importing Ansible at all.

What is kept holds no secret. The API token is a mark there, and read from the vault at each run; every secret-derived
setting is kept only as whether it is set. A snapshot that would hold anything the vault holds is not kept. A run given
extra vars by hand (-e) neither reads nor writes one: it asks Ansible.

A setting per VM - Ansible applies inventory/host_vars/<name>.yml to the VM of that name - is rendered for that VM and
kept with the rest. Everything else is rendered once for a placeholder host, and its name replaced in what comes back.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from . import model

REPO = Path(__file__).resolve().parents[3]
# The name a VM's settings are rendered under: replaced in what comes back. Unlike any DNS label, so that the label made
# from it (tailscale_hostname) is replaced by the VM's own.
PLACEHOLDER = "status_placeholder.vm"
SECRET_MARK = "@@status-proxmox-token@@"
TOKEN_VAR = "vault_proxmox_api_token_secret"
PASSWORD_VAR = "vault_vm_password"
ROLES = ("proxmox_vm", "proxmox_template", "claude_code", "github_projects", "tailscale")

# How the Proxmox API is reached: only asked for with discovery on, since the token behind it is not there without.
API = {
    "url": "{{ proxmox_api_url }}",
    "headers": "{{ proxmox_api_headers }}",
    "validate": "{{ proxmox_validate_certs | bool }}",
    "ca_path": "{{ proxmox_api_ca_path | default('', true) }}",
    "timeout": "{{ status_api_timeout }}",
}
# The run's settings: what the fleet is read with, and the report's own settings, but for the names and sections, which
# each run has of its own.
FLEET = {
    "tag_pattern": "{{ claude_vm_tag_pattern }}",
    "tailscale_tag_pattern": "{{ claude_vm_tailscale_tag_pattern }}",
    "template_name": "{{ proxmox_template_name }}",
    "storage": "{{ proxmox_storage }}",
    "nic": "{{ proxmox_nic }}",
    "ssh_config": "{{ claude_ssh_config | bool }}",
    "ssh_config_file": "{{ claude_ssh_config_file }}",
    "ssh_config_legacy": "{{ claude_ssh_config_legacy_file }}",
    "hosts": "{{ claude_vm_inventory_hosts }}",
    "template_wanted": "{{ proxmox_template_wanted }}",
    "settings": {
        "tag": "{{ claude_vm_tag }}",
        # Every tag a VM of the project's carries as its own, its 1.x name too: what the unfinished tag is read under.
        "tags_owned": "{{ claude_vm_tags_owned }}",
        # The API token's user, whom capacity names in how to grant it what reading a node takes.
        "api_user": "{{ proxmox_api_user }}",
        "template_name": "{{ proxmox_template_name }}",
        "template_rebuild": "{{ proxmox_template_rebuild | bool }}",
        "storage": "{{ proxmox_storage }}",
        "nic": "{{ proxmox_nic }}",
        # Whether Tailscale is the VPN (a promised key, from before the vpn setting), and the setting itself.
        "tailscale_enabled": "{{ tailscale_wanted | default(tailscale_enabled | default(false)) | bool }}",
        "vpn": "{{ claude_vpn_key | default('tailscale' if tailscale_enabled | default(false) | bool else 'none') }}",
        # The setting that turned Tailscale off, as the role's messages name it: tailscale_enabled, or vpn.
        "tailscale_off": "{{ (tailscale_off_setting.name ~ ' is ' ~ tailscale_off_setting.value)"
        " if tailscale_off_setting is defined else 'vpn is none' }}",
        "vm_user": "{{ vm_user }}",
        "discovery": "{{ claude_vm_discovery | bool }}",
        # The editor just code opens, by its key and its name, for the line under ACCESS.
        "editor": "{{ claude_editor_key }}",
        "editor_name": "{{ ((claude_editors | default({}))[claude_editor_key] | default({})).name"
        " | default(claude_editor_key, true) }}",
        # Every editor's name by its key, in the editors list's order, for the windows a VM has open of each.
        "editor_names": "{{ dict(claude_editors | default({}) | dict2items | map(attribute='key')"
        " | zip(claude_editors | default({}) | dict2items | map(attribute='value.name', default=''))) }}",
    },
}
# What the run was given on the command line, kept apart: no snapshot holds these.
ASKED = {"names": "{{ claude_vm_names if vm_name is defined else [] }}", "only": "{{ status_only | default('') }}"}

# What is known of one VM: rendered once for the placeholder, its name replaced in what comes back, or for the VM of a
# host_vars file. Never a secret: only whether each is set.
VM = {
    "drift": {
        "cores": "{{ proxmox_vm_cores | int }}",
        "memory": "{{ proxmox_vm_memory_mb | int }}",
        "balloon": "{{ proxmox_vm_memory_min_mb | int }}",
        "onboot": "{{ proxmox_vm_onboot | bool }}",
        "disk": "{{ proxmox_vm_disk_size }}",
        "ciuser": "{{ proxmox_vm_user }}",
        "sshkeys": "{{ proxmox_vm_ssh_public_keys }}",
        "ipconfig0": "{{ proxmox_vm_ipconfig }}",
        "nameserver": "{{ proxmox_vm_nameservers }}",
        "searchdomain": "{{ proxmox_vm_searchdomain }}",
    },
    # The tag of a VM sized when it was created (_wanted in the engine): its cores, memory and disk are no drift.
    "sized_tag": "{{ proxmox_vm_sized_tag }}",
    "claude": {
        "method": "{{ claude_code_install_method }}",
        "api_key": "{{ claude_code_anthropic_api_key | length > 0 }}",
        "remote_control_wanted": "{{ claude_code_remote_control_wanted | bool }}",
        "unit": "{{ claude_code_remote_control_service }}",
        "login_command": "{{ claude_code_login_command }}",
    },
    "github": {
        "user": "{{ github_projects_user }}",
        "auth": "{{ github_projects_auth | bool }}",
        "auth_method": "{{ github_projects_auth_method }}",
        "token_wanted": "{{ github_projects_token_wanted | bool }}",
        "token_set": "{{ github_projects_token_set | bool }}",
        "configure_command": "{{ github_projects_configure_command }}",
        "login_command": "{{ github_projects_login_command }}",
    },
    "tailscale": {
        "wanted": "{{ tailscale_wanted | bool }}",
        "hostname": "{{ tailscale_hostname }}",
        "ssh": "{{ tailscale_ssh | bool }}",
        "accept_routes": "{{ tailscale_accept_routes | bool }}",
        "accept_dns": "{{ tailscale_accept_dns | bool }}",
        "advertise_tags": "{{ tailscale_advertise_tags }}",
        "login_server": "{{ tailscale_login_server }}",
        "auth_method": "{{ tailscale_auth_method }}",
        "key_wanted": "{{ tailscale_key_wanted | bool }}",
        "key_refused_locally": "{{ tailscale_key_refused_locally | bool }}",
        "joined_tag": "{{ tailscale_joined_tag }}",
        "admin_page": "{{ tailscale_admin_page }}",
        "configure_command": "{{ tailscale_configure_command }}",
        "login_command": "{{ tailscale_login_command }}",
    },
    "vpn": "{{ claude_vpn_key | default('none') }}",
    "projects_dir": "{{ vm_projects_dir | default('', true) }}",
    # What the probe (playbooks/module_utils/status_probe.py) is asked for.
    "asked": {
        "user": "{{ vm_user }}",
        "limit": 25,
        "system": {
            "root": "{{ status_system_root }}",
            "upgrades": "{{ status_read_upgrades | bool }}",
            "timeout": 5,
            "editors": "{{ claude_editors | default({}) | editor_servers }}",
        },
        "claude": {"unit": "{{ claude_code_remote_control_service }}"},
        "github": {
            "marker": "{{ github_projects_marker_file }}",
            "marker_1x": "{{ github_projects_state_dir_1x }}/{{ github_projects_marker_file | basename }}",
        },
        "work": {"dir": "{{ vm_projects_dir | default('', true) }}"},
        "tailscale": {
            "wanted": "{{ tailscale_wanted | bool }}",
            "foreign": "{{ tailscale_foreign_paths }}",
            "marker": "{{ tailscale_installed_marker_path }}",
            "marker_1x": "{{ (tailscale_marker_dir_1x ~ '/' ~ (tailscale_installed_marker_path | basename))"
            " if tailscale_marker_dir_1x | length > 0 else '' }}",
            "joined": "{{ tailscale_joined_marker_path }}",
            "joined_1x": "{{ (tailscale_marker_dir_1x ~ '/' ~ (tailscale_joined_marker_path | basename))"
            " if tailscale_marker_dir_1x | length > 0 else '' }}",
        },
    },
}
# How a VM is reached. A VM of the listing: as add_host used to put it on the host, over ssh, with the host key held.
REACH = {
    "connection": "ssh",
    "user": "{{ vm_user }}",
    "python": "{{ claude_vm_python }}",
    "ssh_args": "{{ status_ssh_args }}",
    "ssh_executable": "{{ ansible_ssh_executable | default('ssh') }}",
    "control_dir": "{{ ansible_control_path_dir | default('~/.ansible/cp') }}",
    "become": "{{ ansible_become | default(true) | bool }}",
    "become_exe": "{{ ansible_become_exe | default('sudo') }}",
}
# A host of the inventory, with discovery off: however it says it is reached.
REACH_INVENTORY = {
    **REACH,
    "connection": "{{ ansible_connection | default('ssh') }}",
    "host": "{{ ansible_host | default(inventory_hostname) }}",
    "user": "{{ ansible_user | default('') }}",
    "port": "{{ ansible_port | default('') }}",
    "key": "{{ ansible_ssh_private_key_file | default('') }}",
    "python": "{{ ansible_python_interpreter | default(claude_vm_python) }}",
    "ssh_args": "{{ ansible_ssh_args | default(lookup('ansible.builtin.config', 'ssh_args', plugin_type='connection',"
    " plugin_name='ssh')) }} {{ ansible_ssh_common_args | default('') }} {{ ansible_ssh_extra_args | default('') }}",
}


class Stale(Exception):
    """A snapshot that cannot be used: the run asks Ansible instead."""


# ------------------------------------------------------------------------------------------- the vault, natively ----


def _vault_open(data: bytes, password: bytes) -> bytes:
    """An ansible-vault AES256 file's text, opened with the standard library and cryptography alone."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    lines = data.strip().splitlines()
    if not (lines and lines[0].startswith((b"$ANSIBLE_VAULT;1.1;AES256", b"$ANSIBLE_VAULT;1.2;AES256"))):
        raise Stale("not an AES256 vault")
    salt, mac, cipher = bytes.fromhex(b"".join(lines[1:]).decode()).split(b"\n", 2)
    derived = hashlib.pbkdf2_hmac("sha256", password, bytes.fromhex(salt.decode()), 10000, 80)
    cipher = bytes.fromhex(cipher.decode())
    if not hmac.compare_digest(hmac.new(derived[32:64], cipher, hashlib.sha256).hexdigest().encode(), mac):
        raise Stale("the vault's password does not open it")
    opened = Cipher(algorithms.AES(derived[:32]), modes.CTR(derived[64:])).decryptor()
    text = opened.update(cipher) + opened.finalize()
    return text[: -text[-1]]


def _vault_values(opts) -> dict:
    import yaml

    data = Path(opts.vault_file).read_bytes()
    if data.startswith(b"$ANSIBLE_VAULT"):
        if not opts.vault_password_file:
            raise Stale("no password for the vault")
        data = _vault_open(data, Path(opts.vault_password_file).read_bytes().strip())
    values = yaml.load(data, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    if not isinstance(values, dict):
        raise Stale("the vault is not a mapping")
    return values


# ---------------------------------------------------------------------------------------------------- Ansible ----


class Ansible:
    """The settings as a play sees them, without a play: the inventory, the vault, the roles' defaults and vars."""

    def __init__(self, opts, say=None):
        from ansible import constants as C
        from ansible import context
        from ansible.cli import CLI
        from ansible.inventory.manager import InventoryManager
        from ansible.parsing.dataloader import DataLoader
        from ansible.playbook.play import Play
        from ansible.plugins.loader import init_plugin_loader
        from ansible.template import Templar, trust_as_template
        from ansible.utils.display import Display
        from ansible.vars.manager import VariableManager

        if say:
            # What Ansible warns of while it reads goes through the live region, or it would land inside the frame.
            Display().display = say
        init_plugin_loader([])
        extra = list(opts.extra)
        if opts.names:
            extra.append(json.dumps({"vm_name": opts.names}))
        if opts.only:
            extra.append(json.dumps({"status_only": opts.only}))
        context._init_global_context(argparse.Namespace(extra_vars=tuple(extra)))
        self.Templar, self.trust, self.Play = Templar, trust_as_template, Play
        self.loader = DataLoader()
        self.loader.set_basedir(str(REPO / "playbooks"))
        if opts.vault_password_file:
            self.loader.set_vault_secrets(
                CLI.setup_vault_secrets(self.loader, vault_ids=[], vault_password_files=[opts.vault_password_file])
            )
        self.inventory = InventoryManager(loader=self.loader, sources=opts.inventory or C.DEFAULT_HOST_LIST)
        # The extra vars come from the global context, set above, as VariableManager reads them.
        self.variables = VariableManager(loader=self.loader, inventory=self.inventory)
        self.host_vars = {host.name: dict(host.vars) for host in self.inventory.get_hosts("all")}
        self.inventory.add_group("status_vms")

    def _play(self, hosts, roles):
        return self.Play.load(
            {"name": "status", "hosts": hosts, "gather_facts": False, "roles": list(roles)},
            variable_manager=self.variables,
            loader=self.loader,
        )

    def trusted(self, value):
        """A value this engine supplies, trusted as a template as a playbook's own text is."""
        if isinstance(value, str):
            return self.trust(value)
        if isinstance(value, dict):
            return {key: self.trusted(one) for key, one in value.items()}
        if isinstance(value, list):
            return [self.trusted(one) for one in value]
        return value

    def render(self, value, variables):
        return self.Templar(loader=self.loader, variables=variables).template(self.trusted(value))

    def local(self):
        """Every variable a task on this machine sees, with each inventory host's own vars as hostvars."""
        play = self._play("localhost", ["proxmox_template"])
        host = self.inventory.get_host("localhost") or self.inventory.localhost
        return {**self.variables.get_vars(play=play, host=host), "hostvars": dict(self.host_vars)}

    def on_host(self, name, inventory_host=False):
        """Every variable a task of a VM sees, the roles' own included. The placeholder and a VM of the listing are
        put in status's own group; a host of the inventory is taken as the inventory has it."""
        if not inventory_host:
            self.inventory.add_host(name, group="status_vms")
        host = self.inventory.get_host(name)
        play = self._play("status_vms" if not inventory_host else "claude_vms", ROLES)
        variables = dict(self.variables.get_vars(play=play, host=host))
        variables.update(
            hostvars=dict(self.host_vars),
            ansible_check_mode=True,
            # The marker's path under the VM user's home, which the probe joins to it.
            github_projects_home_dir=".",
        )
        return variables


# ------------------------------------------------------------------------------------------------- the snapshot ----


def _inventory_files(source: Path):
    """An inventory source and what Ansible reads beside it."""
    folder = source if source.is_dir() else source.parent
    yield from [source] if source.is_file() else folder.glob("*")
    yield from (folder / "group_vars").rglob("*")
    yield from (folder / "host_vars").rglob("*")


def _digest(opts) -> str:
    """Every file the settings are made from, by name, size and time, and what else they read: a change to any gives
    another key, and the settings are asked of Ansible again."""
    digest = hashlib.sha256()
    ssh = Path.home() / ".ssh"
    sources = opts.inventory or [str(REPO / "inventory")]
    groups = [
        [REPO / "ansible.cfg", Path(__file__), Path(opts.vault_password_file or "/nonexistent")],
        *(_inventory_files(Path(source)) for source in sources),
        REPO.glob("roles/*/defaults/main.yml"),
        REPO.glob("roles/*/vars/main.yml"),
        ssh.glob("*.pub"),
    ]
    for path in sorted(str(path) for group in groups for path in group):
        try:
            info = os.stat(path)
        except OSError:
            continue
        if not os.path.isdir(path):
            digest.update(f"{path} {info.st_size} {info.st_mtime_ns}\n".encode())
    seen = ("HOME", "REQUESTS_CA_BUNDLE", "PATH")
    seen += tuple(sorted(k for k in os.environ if k.startswith("ANSIBLE_") and k != "ANSIBLE_LOG_PATH"))
    for name in seen:
        digest.update(f"{name}={os.environ.get(name, '')}\n".encode())
    return digest.hexdigest()


def write_json(path, value) -> None:
    """Replaced whole, only this machine's user may read it. A file that cannot be written is left unwritten."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}")
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else json.dumps(value))
        os.replace(temporary, path)
    except OSError:
        pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _usable(opts):
    """Whether this run may read or write a snapshot: no -e, and a folder to keep it in."""
    return bool(opts.cache and opts.vault_file and not opts.extra)


def _from_snapshot(opts) -> dict:
    kept = read_json(Path(opts.cache) / "settings.json")
    if not isinstance(kept, dict) or kept.get("key") != _digest(opts):
        raise Stale("no snapshot for these files")
    config = kept["config"]
    values = _vault_values(opts)
    secret = str(values[TOKEN_VAR]).strip()
    config["fleet"]["headers"] = {k: v.replace(SECRET_MARK, secret) for k, v in config["fleet"]["headers"].items()}
    config["run"] = {"names": opts.names.split(",") if opts.names else [], "only": opts.only}
    config["password"] = lambda: str(values.get(PASSWORD_VAR) or "")
    return config


def _keep(opts, config, secret, password) -> None:
    """The settings, without the token, unless anything else the vault holds would be written."""
    try:
        values = _vault_values(opts)
    except (OSError, KeyError, ValueError, Stale):
        return
    if (
        not secret
        or str(values.get(TOKEN_VAR, "")).strip() != secret
        or str(values.get(PASSWORD_VAR) or "") != password
    ):
        return
    kept = {key: value for key, value in config.items() if key not in ("run", "password")}
    text = json.dumps({"key": _digest(opts), "config": kept}, default=str).replace(secret, SECRET_MARK)
    if any(isinstance(v, str) and len(v.strip()) >= 6 and v.strip() in text for v in values.values()):
        return
    write_json(Path(opts.cache) / "settings.json", text)


# ----------------------------------------------------------------------------------------------------- loading ----


def _from_ansible(opts, say=None) -> dict:
    """The settings, as Ansible gives them."""
    ansible = Ansible(opts, say)
    local = ansible.local()
    fleet = ansible.render(FLEET, local)
    secret = ""
    if fleet["settings"]["discovery"]:
        fleet.update(ansible.render(API, local))
        fleet["validate"], fleet["timeout"] = bool(fleet["validate"]), float(fleet["timeout"])
        secret = str(ansible.render("{{ proxmox_api_token_secret }}", local))
    run = ansible.render(ASKED, local)
    config = {"fleet": fleet, "run": run}
    placeholder = ansible.on_host(PLACEHOLDER)
    config["vm"] = ansible.render(VM, placeholder)
    config["reach"] = ansible.render(REACH, placeholder)
    password = str(ansible.render("{{ ansible_become_password | default('') }}", placeholder))
    config["password"] = lambda: password
    # A VM with a host_vars file of its own: rendered as itself.
    config["per_vm"] = {}
    host_vars = _host_vars_folder(opts)
    if host_vars is not None:
        for path in sorted(host_vars.iterdir()):
            name = path.stem if path.is_file() else path.name
            if name not in ansible.host_vars and name != "localhost":
                config["per_vm"][name] = ansible.render(VM, ansible.on_host(name))
    # With discovery off, the inventory's claude_vms, each reached as the inventory says.
    config["inventory"] = {}
    if not fleet["settings"]["discovery"]:
        for name in sorted(ansible.inventory.get_groups_dict().get("claude_vms", [])):
            variables = ansible.on_host(name, inventory_host=True)
            config["inventory"][name] = {
                "vm": ansible.render(VM, variables),
                "reach": ansible.render(REACH_INVENTORY, variables),
            }
    elif _usable(opts):
        _keep(opts, config, secret, password)
    return config


def _host_vars_folder(opts):
    for source in opts.inventory or [str(REPO / "inventory")]:
        folder = Path(source)
        folder = folder if folder.is_dir() else folder.parent
        if (folder / "host_vars").is_dir():
            return folder / "host_vars"
    return None


def load(opts, say=None) -> dict:
    """{"fleet", "vm", "reach", "per_vm", "inventory", "run", "password"}: a snapshot, where one holds, or Ansible."""
    if _usable(opts):
        try:
            return _from_snapshot(opts)
        except (KeyError, OSError, TypeError, ValueError, Stale):
            pass
    return _from_ansible(opts, say)


def dns_label(name: str) -> str:
    """A VM's name made one DNS label, as tailscale_hostname makes it."""
    label = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    return re.sub(r"-+$", "", re.sub(r"^-+|-+$", "", label)[:63])


def _named(value, name: str, label: str):
    if isinstance(value, str):
        return value.replace(PLACEHOLDER, name).replace(dns_label(PLACEHOLDER), label)
    if isinstance(value, dict):
        return {key: _named(one, name, label) for key, one in value.items()}
    if isinstance(value, list):
        return [_named(one, name, label) for one in value]
    return value


def for_vm(config: dict, name: str) -> dict:
    """What is known of the VM called ``name``."""
    if name in config["per_vm"]:
        return config["per_vm"][name]
    return _named(config["vm"], name, dns_label(name))


def sections(only) -> list[str]:
    return model.status_sections(only or None)
