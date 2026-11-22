"""The settings table: one typed row for every setting a person may write, written by hand as literals
(docs/architecture/engine.md §16.9).

A row is a setting's name; its type, one of `TYPES`, and its choices, empty where any value of the type is taken; its
default, a literal or, where it is worked out from something else, one of the named functions below; its scope, one
of `SCOPES`: one value for the fleet, one for each host, or one for each machine; its home, one of `HOMES`: the
fleet's `settings.yml`, a host's `settings.yml`, or this computer's own `client.yml` (at scope `host` in its map
`hosts`, by the host's name, and at scope `machine` in its map `machines`, by `<host>/<machine>`); its group, the
role whose variables `settings <role>` lists it with (`common` for a setting that reaches no role's variable, as
today's `just settings` places one); and its description, what a person reads of it.

`ROWS` holds today's documented settings, each under its name, with its type and choices from the argument spec of
the role variable it feeds, its default from the project's defaults or the role's, and its description from the
comment above it in today's example file. `CONNECTION` holds the settings of this computer's own SSH that today's
guides document outside that file, counted apart. Nothing generates this table, and nothing is generated from it into
a tracked file: a change to a row is a change to this file. What it must agree with in today's files is the cutover's
check (tests/unit/test_aw_fleet_registry_today.py), which goes with them; tests/unit/test_aw_fleet_registry.py holds
the table itself.

The overrides, the settings verb and what a play is handed are other modules', which read this one. Standard
library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# The types a row's value takes: a boolean, an integer, a string, a path on this computer, a list of strings, and a
# mapping of strings to the literals YAML reads.
TYPES = ("bool", "int", "str", "path", "list[str]", "dict")
# One value for the whole fleet, one for each host, or one for each machine.
SCOPES = ("global", "host", "machine")
# Where a value is kept: the fleet's settings.yml, a host's settings.yml, or this computer's client.yml.
HOMES = ("fleet", "host", "client")
# What a computed default may read besides the other rows' values, by its parameters' names: the machine's name, and
# this computer's user home.
GIVEN = ("machine", "user_home")


@dataclass(frozen=True)
class Row:
    """One setting: see the module's docstring for what each field holds. A default that is callable is computed: a
    function of this module whose keyword-only parameters name what it reads, each a row's name or one of `GIVEN`.
    A list's default is a tuple and a mapping's a read-only mapping, so that no reader changes the table: a reader
    that hands one on copies it, as a list or a dict."""

    name: str
    type: str
    choices: tuple[str, ...]
    default: object
    scope: str
    home: str
    group: str
    description: str


# ---------------------------------------------------------------- the defaults that are worked out ----


def this_computers_key(*, user_home: Path) -> list[str]:
    """vm_ssh_public_keys' default: this computer's ~/.ssh/id_ed25519.pub, its trailing white space taken off, or no
    key where that file is missing, empty or cannot be read."""
    try:
        text = (user_home / ".ssh" / "id_ed25519.pub").read_text(encoding="utf-8").rstrip()
    except (OSError, UnicodeDecodeError):
        return []
    return [text] if text else []


def same_as_vm_memory_mb(*, vm_memory_mb: int) -> int:
    """vm_memory_min_mb's default: vm_memory_mb's value, a fixed allocation."""
    return vm_memory_mb


def same_as_vm_projects_dir(*, vm_projects_dir: str) -> str:
    """claude_code_remote_control_dir's default: vm_projects_dir's value."""
    return vm_projects_dir


def machine_name(*, machine: str) -> str:
    """claude_code_remote_control_name's default: the machine's name."""
    return machine


def tailnet_label(*, machine: str) -> str:
    """tailscale_hostname's default: the machine's name made one DNS label, as Tailscale needs a machine name -
    lowercased, each run of anything but a letter, a digit or a hyphen made one hyphen, none at either end, at most 63
    characters (today's lib/claude_vm/vpn/tailscale.py's tailscale_label, held equal by the cutover's check)."""
    label = re.sub(r"[^a-z0-9-]+", "-", machine.lower())
    return re.sub(r"-+\Z", "", re.sub(r"^-+|-+\Z", "", label)[:63])


COMPUTED: tuple[Callable[..., object], ...] = (
    this_computers_key,
    same_as_vm_memory_mb,
    same_as_vm_projects_dir,
    machine_name,
    tailnet_label,
)


# ---------------------------------------------------------------- how a row is written ----


def _fleet(name: str, type_: str, default: object, group: str, description: str, choices: tuple = ()) -> Row:
    """A setting kept in the fleet's settings.yml: one value for the whole fleet."""
    return Row(name, type_, choices, default, "global", "fleet", group, description)


def _host(name: str, type_: str, default: object, group: str, description: str, choices: tuple = ()) -> Row:
    """A setting kept in each host's settings.yml: each host's own value, which no other host inherits."""
    return Row(name, type_, choices, default, "host", "host", group, description)


def _client(
    name: str, type_: str, default: object, group: str, description: str, choices: tuple = (), scope: str = "global"
) -> Row:
    """A setting of this computer's own, kept in its client.yml and never in a file that travels."""
    return Row(name, type_, choices, default, scope, "client", group, description)


# ---------------------------------------------------------------- today's documented settings ----

ROWS: tuple[Row, ...] = (
    # Proxmox.
    _host(
        "proxmox_node",
        "str",
        "pve",
        "proxmox_vm",
        "Proxmox node name as shown in the PVE UI: the node that holds the template, where new VMs are cloned. init "
        "writes it in place of the default when the default does not fit the host.",
    ),
    _host(
        "proxmox_storage",
        "str",
        "local-lvm",
        "proxmox_vm",
        "The storage for VM disks, and for the template's. init writes it in place of the default when the default "
        "does not fit the host.",
    ),
    _host(
        "proxmox_bridge",
        "str",
        "vmbr0",
        "proxmox_template",
        "The bridge the template's NIC is attached to, which every clone inherits. Set it before the template is "
        "built, and grant the token SDN.Use on it. init writes it in place of the default when the default does not "
        "fit the host.",
    ),
    _host(
        "proxmox_pool",
        "str",
        "",
        "proxmox_vm",
        "A pool of the project's own on the host, for a token that reaches nothing else. New VMs, the template and "
        "golden templates are made its members, and init grants the token's role on /pool/<name> in place of /vms, "
        "which reaches every VM of the host (docs/getting-started/proxmox-access.md). Empty: none.",
    ),
    _host(
        "proxmox_api_user",
        "str",
        "ansible@pve",
        "proxmox_vm",
        "The API user the token belongs to, realm included, as init makes it (docs/getting-started/proxmox-access.md).",
    ),
    _host(
        "proxmox_api_token_id",
        "str",
        "ansible",
        "proxmox_vm",
        "The API token's name, the part after the ! in user!token, as init makes it "
        "(docs/getting-started/proxmox-access.md).",
    ),
    _host("proxmox_api_port", "int", 8006, "proxmox_vm", "The API's port."),
    _host(
        "proxmox_validate_certs",
        "bool",
        False,
        "common",
        "Check the API's certificate: against the certificate init keeps for the host, in proxmox-trust.pem, or "
        "without one against this machine's trust store, as a certificate from a public CA needs. init writes this "
        "itself, with the file it keeps or where the trust store verifies it; set false while the file is there, the "
        "file alone still turns checking on (docs/getting-started/proxmox-access.md).",
    ),
    _host(
        "proxmox_template_vmid",
        "int",
        9000,
        "proxmox_template",
        "The ID the template every VM is cloned from is built under. On a cluster, build one per node with an ID and "
        "a name of its own (docs/getting-started/requirements.md).",
    ),
    _fleet(
        "proxmox_template_name",
        "str",
        "ubuntu-24.04-cloudinit",
        "proxmox_template",
        "The name of the template every VM is cloned from. On a cluster, build one per node with an ID and a name of "
        "its own (docs/getting-started/requirements.md).",
    ),
    _fleet(
        "proxmox_template_rebuild",
        "bool",
        True,
        "proxmox_template",
        "deploy rebuilds the template when it differs from what its settings would build - CPU type, OS type, "
        "bridge, storage, image, packages. VMs are full clones and do not depend on it. false keeps it as it is, the "
        "differences said: for a template a linked clone still uses, which Proxmox will not remove. A template at "
        "that ID and name with no record of this project's build is refused and left, since it may be yours.",
    ),
    _host(
        "proxmox_template_custom",
        "str",
        "",
        "proxmox_template",
        "A template you built yourself, by its name, already holding your toolchain, for new VMs to clone in place "
        "of the one this project builds. Only read and cloned, never changed; docs/guides/base-image.md says what it "
        "must have.",
    ),
    _fleet(
        "proxmox_template_golden",
        "bool",
        False,
        "proxmox_template",
        "Build a golden template for new VMs, with the toolchain already installed, and clone them from it: "
        "configure, which still runs every task, then finds most of its work done. A deploy that finds none clones "
        "the template above, as without this, and then builds one once its VMs are ready and its editor open, about "
        "two minutes more for the run, while code and login are refused there. A later deploy that finds one has its "
        "VMs ready about a minute sooner, however many it creates. A golden is used for a week. false clones the "
        "template above, and golden templates already made are deleted as they age.",
    ),
    _fleet(
        "proxmox_nic",
        "str",
        "net0",
        "proxmox_vm",
        "Which NIC of the template carries the LAN address: its MAC is how a VM's own address is told apart from "
        "bridges the guest grows later (docker0). Change it only if the template was built with something other than "
        "a single net0.",
    ),
    # VMs.
    _fleet("vm_user", "str", "dev", "proxmox_vm", "Login user on the VM."),
    _fleet(
        "vm_ssh_public_keys",
        "list[str]",
        this_computers_key,  # computed: this computer's ~/.ssh/id_ed25519.pub, as today's lookup reads it
        "proxmox_vm",
        "Public keys cloud-init installs for the login user. Default: this machine's ~/.ssh/id_ed25519.pub; list your "
        "own here if yours is another key.",
    ),
    _fleet(
        "vm_default_name",
        "str",
        "agent-warehouse-default",
        "common",
        "The VM a command acts on when no name is given.",
    ),
    _fleet(
        "vm_cores",
        "int",
        2,
        "proxmox_vm",
        "VM sizing: the cores of every VM a run creates.",
    ),
    _fleet(
        "vm_memory_mb",
        "int",
        4096,
        "proxmox_vm",
        "VM sizing: the memory of every VM a run creates, in MB.",
    ),
    _fleet(
        "vm_memory_min_mb",
        "int",
        same_as_vm_memory_mb,  # computed: vm_memory_mb's value
        "proxmox_vm",
        "The least memory the Proxmox host may leave a VM (its minimum memory, the balloon), from 1 to vm_memory_mb. "
        "Below vm_memory_mb, the host takes the difference back - the VM's cache first - once its own memory use "
        "passes the target whoever runs it set (80% unless changed; a host setting this project never touches), and "
        "gives it back as that use falls. Keep it above what the VM runs at its busiest. Default: vm_memory_mb, a "
        "fixed allocation.",
    ),
    _fleet(
        "vm_disk_size",
        "str",
        "32G",
        "proxmox_vm",
        "VM sizing: the root disk of every VM a run creates, with its unit (K, M, G, T).",
    ),
    _host(
        "vm_nameservers",
        "list[str]",
        (),
        "proxmox_vm",
        "The nameservers cloud-init gives the VMs. Empty leaves them out.",
    ),
    _fleet(
        "vm_searchdomain",
        "str",
        "",
        "proxmox_vm",
        "The DNS search domain cloud-init gives the VMs. Empty leaves it out.",
    ),
    _host(
        "vm_ipconfig",
        "str",
        "ip=dhcp",
        "proxmox_vm",
        "Addressing is DHCP by default and the guest agent reports back what it got. To pin an address instead, set "
        "the cloud-init ipconfig line directly. Every VM a run provisions gets this same address, so use it only for "
        "a single VM, or one VM per run with a new value each time. (A router DHCP reservation is no substitute "
        "across rebuilds - every clone gets a new MAC.)",
    ),
    # GitHub.
    _fleet(
        "github_projects",
        "list[str]",
        (),
        "github_projects",
        "Which of your GitHub repositories to clone. Leave it empty to clone every repository of that user; list "
        "names to clone only those.",
    ),
    _fleet(
        "github_projects_include_forks",
        "bool",
        False,
        "github_projects",
        "Also clone repositories that are forks.",
    ),
    _fleet(
        "github_projects_include_archived",
        "bool",
        False,
        "github_projects",
        "Also clone archived repositories.",
    ),
    _fleet(
        "github_projects_exclude",
        "list[str]",
        (),
        "github_projects",
        "Repository names never cloned, even when github_projects names them.",
    ),
    _fleet(
        "github_projects_clone_protocol",
        "str",
        "https",
        "github_projects",
        "How repositories are cloned: https through gh; ssh needs a key of the user's own on the VM.",
        choices=("https", "ssh"),
    ),
    _fleet(
        "github_auth",
        "bool",
        True,
        "github_projects",
        "Sign gh in on each VM (from the vault's GitHub token, or by GitHub's device flow, which deploy and login run "
        "for you) and make it git's credential helper, so private repositories clone and git push works. false leaves "
        "gh and ~/.gitconfig alone, and a token then only lists: a private repository it lists is skipped, not "
        "cloned.",
    ),
    _fleet(
        "github_auth_method",
        "str",
        "auto",
        "github_projects",
        "auto takes the token when there is one, else the device flow; web is the device flow always, a token then "
        "only listing; token is the token only, and an empty one fails by name.",
        choices=("auto", "web", "token"),
    ),
    _fleet(
        "github_login_auto_start",
        "bool",
        True,
        "common",
        "deploy starts the device flow by itself; false asks first, VM by VM.",
    ),
    _fleet(
        "github_login_scopes",
        "list[str]",
        (),
        "github_projects",
        "Scopes the device flow asks for on top of gh's own (repo, read:org, gist). None by default: [workflow] lets "
        "the VM's agent push a change to .github/workflows/, which is the road to a repository's Actions secrets; "
        "without it GitHub refuses such a push, naming the scope. A login made before keeps the scopes it has.",
    ),
    _fleet(
        "git_user_name",
        "str",
        "",
        "github_projects",
        "The git identity's name on each VM. Empty: your GitHub display name (or login).",
    ),
    _fleet(
        "git_user_email",
        "str",
        "",
        "github_projects",
        "The git identity's email on each VM. Empty: GitHub's noreply address for your account, so no private email "
        "lands in commits by default.",
    ),
    # Packages and tools.
    _fleet(
        "vm_extra_packages",
        "list[str]",
        (),
        "common",
        "More packages to install on the VM, on top of the ones every VM has.",
    ),
    _fleet("dev_tools_install_docker", "bool", True, "dev_tools", "Install Docker Engine on the VM."),
    _fleet("dev_tools_install_node", "bool", True, "dev_tools", "Install Node.js on the VM."),
    _fleet("dev_tools_install_uv", "bool", True, "dev_tools", "Install uv on the VM."),
    _fleet("dev_tools_install_gh", "bool", True, "dev_tools", "Install the GitHub CLI, gh, on the VM."),
    _fleet(
        "dev_tools_install_rust",
        "bool",
        False,
        "dev_tools",
        "Rust: rustup, and a stable toolchain with clippy and rustfmt for the login user. Off by default.",
    ),
    _fleet("dev_tools_node_major", "int", 22, "dev_tools", "The Node.js major version."),
    # The editor and SSH.
    _client(
        "editor",
        "str",
        "vscode",
        "common",
        "The editor code opens, and deploy at its end: one at a time, vscode by default (docs/guides/vscode.md), or "
        "zed (docs/guides/zed.md). For one run, code's --editor.",
        choices=("vscode", "zed"),
    ),
    _fleet(
        "vscode_remote",
        "bool",
        True,
        "vscode_server",
        "An editor over SSH. On the VM, whichever editor is chosen: an sshd drop-in with forwarding (which Claude's "
        "sign-in in the browser uses too) and keepalives, checked against what sshd runs with, and a higher inotify "
        "limit. Set false to remove both files.",
    ),
    _client(
        "claude_ssh_config",
        "bool",
        True,
        "common",
        "On this machine: an SSH alias per VM in ~/.ssh/agent-warehouse.conf, included from the top of ~/.ssh/config, "
        "so `ssh alpha` and your editor follow the VM's address; destroy removes a VM's. Set false to keep out of "
        "~/.ssh entirely.",
    ),
    _client(
        "claude_ssh_config_include",
        "bool",
        True,
        "common",
        "false writes the aliases' file but not the Include line at the top of ~/.ssh/config, which the run then "
        "prints for you to add.",
    ),
    _client(
        "claude_ssh_host_domain",
        "str",
        "",
        "common",
        "Point aliases at <name>.<domain> instead of the address, on a network whose DNS resolves VM names (a router "
        "that registers DHCP leases, or avahi with local); then a lease that moves no longer matters. Not used "
        "through the Proxmox host. Each host's own, never another's.",
        scope="host",
    ),
    _client(
        "claude_ssh_identity_file",
        "str",
        "",
        "common",
        "The key each alias names (IdentityFile). Empty lets ssh pick, which finds the default key vm_ssh_public_keys "
        "reads.",
    ),
    _client(
        "claude_ssh_forward_agent",
        "bool",
        False,
        "common",
        "Off on purpose: the VM user is root-equivalent and runs Claude Code, and agent forwarding hands them every "
        "key in your agent. Pushes go through gh, signed in on the VM itself.",
    ),
    _client(
        "claude_ssh_options",
        "dict",
        MappingProxyType({"ServerAliveInterval": "30"}),
        "common",
        'Extra "Key value" lines written into each alias, as they are. ServerAliveInterval keeps the editor\'s '
        "connection from going stale behind a NAT or a laptop that slept.",
    ),
    _host(
        "vm_ssh_route",
        "str",
        "auto",
        "common",
        "How this machine reaches the VMs. auto: through the Proxmox host's own sshd, as a forward, where the host is "
        "reached on a tailnet (ssh dials it at a 100.64.0.0/10 or fd7a:115c:a1e0::/48 address), straight to each "
        "VM's address otherwise. host: always through it; direct: never. Through the host the aliases carry it as a "
        "ProxyJump line, and claude_ssh_host_domain is not used (docs/guides/remote-access.md).",
        choices=("auto", "direct", "host"),
    ),
    _fleet(
        "vm_projects_dir",
        "str",
        "",
        "claude_code",
        "Where the code lives on the VM: cloned into, opened by Remote Control, and what the editor opens. Empty means "
        "~/projects of the VM user.",
    ),
    _client(
        "editor_claude_terminal",
        "bool",
        True,  # today's default is the 1.0 name's, vscode_claude_terminal, which has no row: its literal
        "common",
        "In VS Code, code opens a workspace file that starts claude in a terminal of the window, once you have "
        "trusted the folder and allowed automatic tasks. In Zed (docs/guides/zed.md) it writes a task, Claude, which "
        'the window of the projects directory lists under "task: spawn", and the helper it runs: nothing starts by '
        "itself there. Set false to open the bare folder and write none of them.",
    ),
    _client(
        "editor_files_dir",
        "str",
        "",  # today's default is the 1.0 name's, vscode_workspaces_dir, which has no row: its literal
        "common",
        "Where those files go on the VM, as an absolute path of its own, never inside a repository: code refuses one "
        "in a checkout of vm_projects_dir. Empty: .agent-warehouse in vm_projects_dir. A directory that already "
        "exists keeps its mode; files written before a move stay where they were.",
    ),
    _client(
        "editor_session_notes",
        "bool",
        True,  # today's default is the 1.0 name's, vscode_session_notes, which has no row: its literal
        "common",
        "A page about the VM - addresses, what is signed in, the commands - that code writes on the VM. In VS Code it "
        "opens beside the Claude terminal each time the window opens, its sign-in status read from the VM then, so it "
        "needs editor_claude_terminal; in Zed it is written without that refresh, for you to open once.",
    ),
    _client(
        "claude_sessions_sync",
        "bool",
        True,  # today's default is the 1.0 name's, vscode_claude_sessions, which has no row: its literal
        "common",
        "Copy this machine's Claude Code sessions of the VM's repositories - matched by git origin - the last 30 days "
        "of them, to the VM on every code, so /resume there lists them (Ctrl+A for every project). Their transcripts "
        "hold whatever their tools printed: false keeps them here, though --session still copies its one session.",
    ),
    _client(
        "claude_sessions_sync_max_size",
        "str",
        "256M",
        "common",
        "The most one copy of those sessions sends, a size as vm_disk_size is written (256M, 1G; a number alone is "
        "gigabytes): the newest first, each with its tool results while it fits, and the rest said and left here. 0 "
        "for no bound. It counts what the VM does not have yet: a first copy sends each whole, a later one what "
        "changed.",
    ),
    _client(
        "claude_sessions_sync_subagents",
        "bool",
        True,
        "common",
        "Copy each session's subagent transcripts with it, so a subagent it started can be read, and continued, on "
        "the VM. They count toward the bound above, and a long session's subagents can hold more than its own "
        "transcript.",
    ),
    _client(
        "claude_memory_sync",
        "bool",
        True,
        "common",
        "Copy this machine's auto memory of each repository the VM has a checkout of - matched by git origin, as the "
        "sessions are - into that checkout's memory folder on the VM on every code, whether or not the sessions go, "
        "with a note saying where this machine's paths are there. A note changed on the VM is never overwritten. "
        "false keeps the notes here.",
    ),
    _client(
        "claude_instructions_sync",
        "bool",
        True,
        "common",
        "Copy this machine's global instructions, ~/.claude/CLAUDE.md, and the files of ~/.claude/rules/ to the VM's, "
        "unless claude_code_global_instructions sets the VM's CLAUDE.md. A file changed on the VM is never "
        "overwritten.",
    ),
    _client(
        "claude_settings_sync",
        "bool",
        True,
        "common",
        "Copy this machine's portable Claude Code settings into the VM's ~/.claude/settings.json: the model, effort "
        "and preferences, the permission rules and mode, and auto mode's rules - never a credential, a program, this "
        "machine's paths, or a key claude_code_settings sets. A setting changed on the VM (/model there) is never "
        "overwritten.",
    ),
    _client(
        "claude_settings_sync_keys",
        "list[str]",
        (),
        "common",
        "More top-level settings to copy that way, such as statusLine or hooks, which run a program and stay here "
        "otherwise; taken out of the list, one goes from the VM while still as copied. A setting code never copies - "
        "env, apiKeyHelper, this machine's paths - is refused.",
    ),
    _client(
        "claude_plugins_sync",
        "bool",
        True,
        "common",
        "Install on the VM, by its own Claude Code, the plugins installed and enabled for you here from a marketplace "
        "on GitHub - claude plugin install there, never accepting a command a marketplace declares for you. Nothing "
        "is ever uninstalled there.",
    ),
    _client(
        "claude_history_sync",
        "bool",
        True,
        "common",
        "Add the prompts typed in the sessions copied to the VM's prompt history, for the up arrow and Ctrl+R there; "
        "with the sessions only, never twice.",
    ),
    _client(
        "claude_extensions_sync",
        "bool",
        True,
        "common",
        "Copy your user-level skills, subagents, commands, output styles, workflows, themes and keybindings "
        "(~/.claude/skills/ and the like) to the VM's, but the skills your account syncs, which the VM downloads at "
        "its own sign-in. A file changed on the VM is never overwritten.",
    ),
    _client(
        "vscode_user_settings_file",
        "str",
        "",
        "common",
        "The VS Code settings read (never written) to say whether the terminal starts on its own. Empty: where stock "
        "VS Code keeps them on macOS and Linux; set it for Insiders, VSCodium, or a VS Code on Windows driven from "
        "WSL.",
    ),
    # Claude Code.
    _fleet(
        "claude_code_version",
        "str",
        "stable",
        "claude_code",
        "Claude Code release channel: stable, latest, or an exact version of 2.1.89 or later, the first release "
        "Anthropic signs, like 2.1.311. A channel is the update setting written into ~/.claude/settings.json; what is "
        "installed is always the release it names, its signature checked here.",
    ),
    _fleet(
        "claude_code_settings",
        "dict",
        MappingProxyType({}),
        "claude_code",
        "Extra user-level settings merged into ~/.claude/settings.json.",
    ),
    _fleet(
        "claude_code_remote_control",
        "bool",
        True,
        "claude_code",
        "Remote Control: put each VM's Claude Code session in the list at claude.ai/code and in the mobile app. On by "
        "default, and derived from the credentials on the VM (docs/guides/remote-control.md). Set false to turn it off "
        "- also the answer when claude_code_settings.env carries a privacy setting such as DISABLE_TELEMETRY or "
        "DO_NOT_TRACK on purpose: those disable Remote Control, so the role leaves the service stopped and says so "
        "until one or the other goes.",
    ),
    _fleet(
        "claude_code_login_args",
        "list[str]",
        (),
        "claude_code",
        "Extra arguments for the Claude sign-in's `claude auth login`, such as [--sso].",
    ),
    _fleet(
        "claude_code_login_timeout",
        "int",
        120,
        "claude_code",
        "How long, in seconds, the Claude sign-in may take to accept a code you paste.",
    ),
    _fleet(
        "claude_code_login_approval_timeout",
        "int",
        300,
        "claude_code",
        "How long, in seconds, the run waits for your browser to come back to the VM by itself once you authorise.",
    ),
    _fleet(
        "claude_code_remote_control_name",
        "str",
        machine_name,  # computed: the machine's name
        "claude_code",
        "Session title in the list at claude.ai/code; defaults to the VM name.",
    ),
    _fleet(
        "claude_code_remote_control_dir",
        "str",
        same_as_vm_projects_dir,  # computed: vm_projects_dir's value
        "claude_code",
        "Where sessions open. Empty means ~/projects of the VM user, where github_projects clones; it must not be the "
        "home directory itself.",
    ),
    _fleet(
        "claude_code_remote_control_permission_mode",
        "str",
        "default",
        "claude_code",
        "Permission mode remote sessions start in, passed as --permission-mode, which outranks "
        "permissions.defaultMode in claude_code_settings.",
    ),
    _fleet(
        "claude_code_remote_control_spawn",
        "str",
        "same-dir",
        "claude_code",
        "same-dir: every session shares the directory above. worktree: each session gets its own git worktree (needs "
        "a repository there). session: serve one session and reject further connections.",
        choices=("same-dir", "worktree", "session"),
    ),
    # Sign-ins and teardown.
    _client(
        "deploy_sign_in",
        "bool",
        True,
        "common",
        "deploy ends with the VM signed in to Claude, GitHub and Tailscale and open in your editor. The sign-ins are "
        "the steps only you can take, each a page in your browser that the run waits for you at, one VM at a time. "
        "Claude's comes back to the VM by itself once you authorise (or takes the code the page shows, pasted at the "
        "prompt), GitHub's has its code on your clipboard, and Tailscale's asks you to connect the device. GitHub's "
        "and Tailscale's start by themselves (github_login_auto_start, tailscale_login_auto_start) for every VM but "
        "one whose Claude sign-in nobody answered, and none runs where a vault token or key signs the VM in. A run "
        "with no terminal skips them all. false leaves them to login.",
    ),
    _client(
        "deploy_code",
        "bool",
        True,
        "common",
        "Open the VM in the editor at the end of a one-VM deploy; a deploy of several ends with each VM's line for "
        "code.",
    ),
    _fleet(
        "destroy_release",
        "bool",
        True,
        "common",
        "Before destroy deletes a VM, sign it out of Claude - stopping Remote Control - and of gh, and take it off "
        "your tailnet, its device deleted with tailscale_api_auth_method. gh's token stays valid: revoking GitHub CLI "
        "at GitHub signs out every gh you have, so the report says where, and destroy does not. A stopped VM is "
        "started for it. false deletes the VMs without releasing them, their sessions still saved first.",
    ),
    _fleet(
        "destroy_save_sessions",
        "bool",
        True,
        "common",
        "And before that, save what of each VM's Claude Code sessions exists only on its disk - those begun there, "
        "the copies code made that were continued there, and their auto memory - into destroy_sessions_dir here, as "
        "the VM had them, plain files and folders only; `claude --resume <path>` continues one. A VM whose save fails "
        "is kept, neither released nor deleted. false deletes them with the VM.",
    ),
    _client(
        "destroy_sessions_dir",
        "str",
        "",
        "common",
        "A folder per VM and run, <name>-<YYYYMMDD-HHMMSS>, 0700, in an absolute path or one starting with ~. Empty: "
        ".sessions/ in this checkout (git-ignored). The transcripts hold whatever their tools printed, and stay until "
        "you delete them.",
    ),
    _fleet(
        "destroy_sessions_max_size",
        "str",
        "4G",
        "common",
        "The most one VM's save may write, whatever the VM sends (4G, 500M): past it the save fails and the VM is "
        "kept, what arrived before left in its folder.",
    ),
    _client(
        "login_desktop",
        "bool",
        True,
        "claude_code",
        "Open the sign-in pages (and copy GitHub's code) on this machine. Off, the run only prints them, for a "
        "controller with no desktop.",
    ),
    # The VPN.
    _fleet(
        "vpn",
        "str",
        "none",  # today's default follows tailscale_enabled, which has no row: none, its value unset
        "common",
        "The VPN each VM joins: tailscale or none (docs/guides/remote-access.md). none is right for a VPN you "
        "already run that routes this LAN - WireGuard on your router, a Tailscale subnet router - which reaches the "
        "VMs at their LAN addresses. Every VM deployed from then on follows it; none never takes Tailscale off a VM it "
        "is on, which is left as it is, unmanaged. init writes your answer.",
        choices=("none", "tailscale"),
    ),
    # Tailscale.
    _fleet(
        "tailscale_auth_method",
        "str",
        "auto",
        "tailscale",
        "How a VM joins: auto is the vault's Tailscale auth key when set, else the web login; key is the key only; "
        "web is the web login only.",
        choices=("auto", "key", "web"),
    ),
    _fleet(
        "tailscale_login_auto_start",
        "bool",
        True,
        "common",
        "deploy starts the web login by itself; false asks first, VM by VM.",
    ),
    _fleet(
        "tailscale_api_auth_method",
        "str",
        "auto",
        "tailscale",
        "Delete a VM's device from your tailnet when destroy deletes the VM. auto uses whichever credential the vault "
        "holds - its Tailscale API token first, else its OAuth client (devices:core) - and only logs the VM out with "
        "neither; none only logs it out; token and oauth use that one only. An OAuth client reaches only the devices "
        "with its tags, so it may not delete one joined by the web login.",
        choices=("auto", "none", "token", "oauth"),
    ),
    _fleet(
        "tailscale_ssh",
        "bool",
        False,
        "tailscale",
        "Tailscale SSH on the tailnet address (needs an ssh rule in your tailnet policy).",
    ),
    _fleet(
        "tailscale_accept_routes",
        "bool",
        False,
        "tailscale",
        "Use the subnet routes other devices on your tailnet advertise.",
    ),
    _fleet(
        "tailscale_accept_dns",
        "bool",
        False,
        "tailscale",
        "Let your tailnet's DNS settings change the VM's resolver. Off, the VM keeps the one DHCP gave it, and your "
        "devices still reach it by its tailnet name; on, a tailnet that overrides local DNS sends all its lookups to "
        "the tailnet's nameservers (docs/guides/tailscale.md).",
    ),
    _fleet(
        "tailscale_advertise_tags",
        "list[str]",
        (),
        "tailscale",
        "Tags asked for at the join; leave empty with a tagged key.",
    ),
    _fleet(
        "tailscale_login_server",
        "str",
        "",
        "tailscale",
        "A control server of your own, such as Headscale. Empty: Tailscale's.",
    ),
    _fleet(
        "tailscale_upload_logs",
        "bool",
        True,
        "tailscale",
        "false keeps tailscaled from uploading its logs to Tailscale, which also ends Tailscale's technical support "
        "for the VMs.",
    ),
    _fleet(
        "tailscale_login_timeout",
        "int",
        300,
        "tailscale",
        "Seconds the web login waits for your approval. The page stays valid for 7 days, and a VM approved later "
        "joins by itself.",
    ),
    _fleet(
        "tailscale_hostname",
        "str",
        tailnet_label,  # computed: the machine's name made one DNS label
        "tailscale",
        "The machine name, when the VM's own name is not the one you want there.",
    ),
    _fleet(
        "tailscale_up_args",
        "list[str]",
        (),
        "tailscale",
        "More flags for a join.",
    ),
    _fleet(
        "tailscale_daemon_flags",
        "list[str]",
        (),
        "tailscale",
        "More flags for tailscaled itself.",
    ),
)


# ---------------------------------------------------------------- this computer's own SSH ----

# The settings of this computer's ssh that today's guides document outside the example file: the key and the ssh the
# Proxmox host's entry names today (ansible_ssh_private_key_file, ansible_ssh_executable), and a person's own options
# and jump for the machines (ansible_ssh_common_args, for every host, for the VMs or for one VM). The host's account,
# port, address and jump are fields of its host.yml, not rows.
CONNECTION: tuple[Row, ...] = (
    _client(
        "ssh_identity_file",
        "path",
        "",
        "connection",
        "The key this computer's ssh offers the Proxmox host, for its root SSH and as the hop to its VMs. Empty: the "
        "one ssh picks, from ~/.ssh/config or its own defaults. A key's path is one computer's, kept for each host.",
        scope="host",
    ),
    _client(
        "ssh_executable",
        "path",
        "",
        "connection",
        "The ssh program every connection runs, by its path. Empty: the one found on PATH.",
    ),
    _client(
        "vm_ssh_options",
        "dict",
        MappingProxyType({}),
        "connection",
        "Options of your own for the ssh connections to the machines, by name and value, for every machine or for "
        "one. A jump among them (ProxyJump, or a ProxyCommand) is that machine's route, and no hop is added to it. An "
        "option the product writes, and one that would undo what every connection holds, is refused by name.",
        scope="machine",
    ),
)


# ---------------------------------------------------------------- the reader ----

_ALL = ROWS + CONNECTION
_BY_NAME = {each.name: each for each in _ALL}
# Each group's name, in the order its first row comes.
GROUPS = tuple(dict.fromkeys(each.group for each in _ALL))


def rows() -> tuple[Row, ...]:
    """Every row: today's documented settings, then this computer's own SSH."""
    return _ALL


def row(name: str) -> Row:
    """The row of a setting, by its name; KeyError for a name the table has no row for."""
    return _BY_NAME[name]


def group(name: str) -> tuple[Row, ...]:
    """The rows of a group, in the table's order; KeyError for a name that is no group."""
    if name not in GROUPS:
        raise KeyError(name)
    return tuple(each for each in _ALL if each.group == name)
