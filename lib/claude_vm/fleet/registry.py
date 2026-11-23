"""The settings table: one typed row for every setting a person may write, written by hand as literals
(docs/architecture/engine.md §16.9).

A row is a setting's name; its type, one of `TYPES`, and its choices, empty where any value of the type is taken; its
default, a literal or, where it is worked out from something else, one of the named functions below; its scope, one
of `SCOPES`: one value for the fleet, one for each host, or one for each machine; its home, one of `HOMES`: the
fleet's `settings.yml` (at scope `machine` in its map `machines`, by `<host>/<machine>`), a host's `settings.yml`, this
computer's own `client.yml` (at scope `host` in its map `hosts`, by the host's name, and at scope `machine` in its map
`machines`, by `<host>/<machine>`), or `run`, no file at all: a value given with `--set` for one run alone; its group,
the role whose variables `settings <role>` lists it with (`common` for a setting that reaches no role's variable, as
today's `just settings` places one); its description, what a person reads of it; whether its value may hold a secret
(`secret`), so that it is never shown, never said back and never taken for one run; whether an agent may ask to change
it (`agent`), which the agents' server reads; for such a setting whose type lets more through than an agent may
give, the values an agent may give it (`values`); and, where its readers need more than its type, the form every value
of a string must have (`form`) or the least an integer may be (`least`), which fleet/overrides.py holds a value to. A
row that names a secret and that an agent may change cannot be built.

`ROWS` holds today's documented settings that the new engine keeps as settings, each under its name, with its type and
choices from the argument spec of the role variable it feeds, its default from the project's defaults or the role's,
and its description from the comment above it in today's example file. `ADDED` holds the settings the new engine adds,
which today's example does not document, counted apart, and `CONNECTION` the settings of this computer's own SSH that
today's guides document outside that file, counted apart too. Nothing generates this table, and nothing is generated
from it into a tracked file: a change to a row is a change to this file. What it must agree with in today's files is
the cutover's check (tests/unit/test_aw_fleet_registry_today.py), which goes with them, and which names today's
settings that have no row; tests/unit/test_aw_fleet_registry.py holds the table itself.

The overrides, the settings verb, the agents' server and what a play is handed are other modules', which read this
one. Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# The types a row's value takes: a boolean, an integer, a string, a path on this computer, a list of strings, a
# mapping of strings to the literals YAML reads, and a size with its unit. A path is read on this computer: `~` at its
# start is the user's home, a path still relative after that is refused by name, and `""` is unset, which each such
# row's description says the meaning of. A size is a whole number with G or T after it, capital or small, as deploy's
# --disk takes one, kept with its unit capital, so that 128g and 128G are one size (fleet/overrides.py's typed).
TYPES = ("bool", "int", "str", "path", "list[str]", "dict", "size")
# One value for the whole fleet, one for each host, or one for each machine.
SCOPES = ("global", "host", "machine")
# Where a value is kept: the fleet's settings.yml, a host's settings.yml, this computer's client.yml, or no file: a
# value given for one run alone, which a file that holds it is refused for.
HOMES = ("fleet", "host", "client", "run")
# What a computed default may read besides the other rows' values, by its parameters' names: the machine's name, this
# computer's user home, and the key file fleet.paths names for the running program's home.
GIVEN = ("machine", "user_home", "key_file")


@dataclass(frozen=True)
class ValueRule:
    """Values a pattern matches whole, said as `said`: the values an agent may give a setting whose type lets more
    through (a row's `values`), or the form every value of a string must have (a row's `form`)."""

    pattern: str
    said: str

    def allows(self, value: str) -> bool:
        return re.fullmatch(self.pattern, value) is not None


@dataclass(frozen=True)
class Row:
    """One setting: see the module's docstring for what each field holds. A default that is callable is computed: a
    function of this module whose keyword-only parameters name what it reads, each a row's name or one of `GIVEN`.
    A list's default is a tuple and a mapping's a read-only mapping, so that no reader changes the table: a reader
    that hands one on copies it, as a list or a dict. A row that names a secret is never one an agent may change, and
    only a row an agent may change has a rule for its values: either is refused as the row is made."""

    name: str
    type: str
    choices: tuple[str, ...]
    default: object
    scope: str
    home: str
    group: str
    description: str
    secret: bool = False
    agent: bool = False
    values: ValueRule | None = None
    form: ValueRule | None = None
    least: int | None = None

    def __post_init__(self) -> None:
        if self.secret and self.agent:
            raise ValueError(f"{self.name} names a secret, so no agent may change it")
        if self.values is not None and not self.agent:
            raise ValueError(f"{self.name} has a rule for an agent's values, but no agent may change it")
        if self.form is not None and self.type != "str":
            raise ValueError(f"{self.name} has a form, which only a string's value is held to")
        if self.least is not None and self.type != "int":
            raise ValueError(f"{self.name} has a least value, which only an integer's is held to")


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


def the_homes_key_file(*, key_file: Path) -> str:
    """vault_key_file's default: the key file fleet.paths names for the running program's home, in a folder of this
    computer's own that is neither the fleet's folder a person copies nor the state folder a cleanup may remove."""
    return str(key_file)


COMPUTED: tuple[Callable[..., object], ...] = (
    this_computers_key,
    same_as_vm_memory_mb,
    same_as_vm_projects_dir,
    machine_name,
    tailnet_label,
    the_homes_key_file,
)


# ---------------------------------------------------------------- what an agent may give ----

# claude_code_version from an agent: a channel or a release, never another spec, since with npm as the install method
# the value is the package's version for npm, which installs any spec given there (another package, a repository, a
# tarball's address) as root on every VM.
VERSION_FROM_AN_AGENT = ValueRule(r"stable|latest|[0-9]+\.[0-9]+\.[0-9]+", "stable, latest or a version as 2.1.319")


# ---------------------------------------------------------------- what a value must be beyond its type ----

# vm_label: one tag as Proxmox takes one (pve-common's pve-tag format), which a , or a ; would make two; held equal to
# pve/client.py's TAG, which the writes send, by tests/unit/test_aw_fleet_registry.py.
PROXMOX_TAG = ValueRule(
    r"[A-Za-z0-9_][A-Za-z0-9_+.-]{0,99}",
    "one Proxmox tag: letters, digits, _, +, . and -, starting with a letter, a digit or _",
)
# github_username: a GitHub account's name as init --github-user takes one (schema/patterns.py's github_user, held
# equal by the same test), or none.
GITHUB_USER = ValueRule(r"[A-Za-z0-9-]*", "a GitHub account's name: letters, digits and hyphens")


# ---------------------------------------------------------------- how a row is written ----


def _fleet(
    name: str,
    type_: str,
    default: object,
    group: str,
    description: str,
    choices: tuple = (),
    scope: str = "global",
    *,
    secret: bool = False,
    agent: bool = False,
    values: ValueRule | None = None,
    form: ValueRule | None = None,
    least: int | None = None,
) -> Row:
    """A setting kept in the fleet's settings.yml: one value for the whole fleet, or at scope machine one for each
    machine, in the file's map machines."""
    return Row(name, type_, choices, default, scope, "fleet", group, description, secret, agent, values, form, least)


def _host(name: str, type_: str, default: object, group: str, description: str, choices: tuple = ()) -> Row:
    """A setting kept in each host's settings.yml: each host's own value, which no other host inherits."""
    return Row(name, type_, choices, default, "host", "host", group, description)


def _client(
    name: str,
    type_: str,
    default: object,
    group: str,
    description: str,
    choices: tuple = (),
    scope: str = "global",
    *,
    agent: bool = False,
    least: int | None = None,
) -> Row:
    """A setting of this computer's own, kept in its client.yml (at scope host in its map hosts, by the host's name)
    and never in a file that travels."""
    return Row(name, type_, choices, default, scope, "client", group, description, agent=agent, least=least)


def _run(name: str, type_: str, default: object, group: str, description: str) -> Row:
    """A setting for one run alone, given with --set and kept in no file."""
    return Row(name, type_, (), default, "global", "run", group, description)


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
        "The pool on the host the fleet's VMs, its template and its golden templates are members of, and where the "
        "token's role is granted, with VM.Audit on /vms beside it to read the node's other VMs (proxmox_audit_scope). "
        "Empty: the fleet's own pool, which init makes.",
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
        agent=True,
    ),
    _fleet(
        "vm_cores",
        "int",
        2,
        "proxmox_vm",
        "VM sizing: the cores of every VM a run creates.",
        agent=True,
    ),
    _fleet(
        "vm_memory_mb",
        "int",
        4096,
        "proxmox_vm",
        "VM sizing: the memory of every VM a run creates, in MB.",
        agent=True,
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
        agent=True,
    ),
    _fleet(
        "vm_disk_size",
        "size",
        "32G",
        "proxmox_vm",
        "VM sizing: the root disk of every VM a run creates, a whole number with its unit, G or T, capital or small.",
        agent=True,
    ),
    _host(
        "vm_nameservers",
        "list[str]",
        (),
        "proxmox_vm",
        "The nameservers cloud-init gives the VMs. Empty leaves them out.",
    ),
    _host(
        "vm_searchdomain",
        "str",
        "",
        "proxmox_vm",
        "The DNS search domain cloud-init gives the VMs. Empty leaves it out. One network's, so each host's own.",
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
        agent=True,
    ),
    _fleet(
        "github_projects_include_forks",
        "bool",
        False,
        "github_projects",
        "Also clone repositories that are forks.",
        agent=True,
    ),
    _fleet(
        "github_projects_include_archived",
        "bool",
        False,
        "github_projects",
        "Also clone archived repositories.",
        agent=True,
    ),
    _fleet(
        "github_projects_exclude",
        "list[str]",
        (),
        "github_projects",
        "Repository names never cloned, even when github_projects names them.",
        agent=True,
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
        agent=True,
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
    _fleet("dev_tools_install_docker", "bool", True, "dev_tools", "Install Docker Engine on the VM.", agent=True),
    _fleet("dev_tools_install_node", "bool", True, "dev_tools", "Install Node.js on the VM.", agent=True),
    _fleet("dev_tools_install_uv", "bool", True, "dev_tools", "Install uv on the VM.", agent=True),
    _fleet("dev_tools_install_gh", "bool", True, "dev_tools", "Install the GitHub CLI, gh, on the VM.", agent=True),
    _fleet(
        "dev_tools_install_rust",
        "bool",
        False,
        "dev_tools",
        "Rust: rustup, and a stable toolchain with clippy and rustfmt for the login user. Off by default.",
        agent=True,
    ),
    _fleet("dev_tools_node_major", "int", 22, "dev_tools", "The Node.js major version.", agent=True),
    # The editor and SSH.
    _client(
        "editor",
        "str",
        "vscode",
        "common",
        "The editor code opens, and deploy at its end: one at a time, vscode by default (docs/guides/vscode.md), or "
        "zed (docs/guides/zed.md). For one run, code's --editor.",
        choices=("vscode", "zed"),
        agent=True,
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
        "path",
        "",
        "common",
        "The key each alias names (IdentityFile). Empty lets ssh pick, which finds the default key vm_ssh_public_keys "
        "reads. ~ at its start is your home; a relative path is refused by name.",
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
    _client(
        "vm_ssh_route",
        "str",
        "auto",
        "common",
        "How this machine reaches the VMs. auto: through the Proxmox host's own sshd, as a forward, where the host is "
        "reached on a tailnet (ssh dials it at a 100.64.0.0/10 or fd7a:115c:a1e0::/48 address), straight to each "
        "VM's address otherwise. host: always through it; direct: never. Through the host the aliases carry it as a "
        "ProxyJump line, and claude_ssh_host_domain is not used (docs/guides/remote-access.md). This computer's own "
        "for each host: another computer that copies the fleet's folder reaches the VMs its own way.",
        choices=("auto", "direct", "host"),
        scope="host",
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
        agent=True,
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
        agent=True,
    ),
    _client(
        "claude_sessions_sync",
        "bool",
        True,  # today's default is the 1.0 name's, vscode_claude_sessions, which has no row: its literal
        "common",
        "Copy this machine's Claude Code sessions of the VM's repositories - matched by git origin - the last 30 days "
        "of them, to the VM on every code, so /resume there lists them (Ctrl+A for every project). Their transcripts "
        "hold whatever their tools printed: false keeps them here, though --session still copies its one session.",
        agent=True,
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
        agent=True,
    ),
    _client(
        "claude_sessions_sync_subagents",
        "bool",
        True,
        "common",
        "Copy each session's subagent transcripts with it, so a subagent it started can be read, and continued, on "
        "the VM. They count toward the bound above, and a long session's subagents can hold more than its own "
        "transcript.",
        agent=True,
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
        agent=True,
    ),
    _client(
        "claude_instructions_sync",
        "bool",
        True,
        "common",
        "Copy this machine's global instructions, ~/.claude/CLAUDE.md, and the files of ~/.claude/rules/ to the VM's, "
        "unless claude_code_global_instructions sets the VM's CLAUDE.md. A file changed on the VM is never "
        "overwritten.",
        agent=True,
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
        agent=True,
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
        agent=True,
    ),
    _client(
        "claude_history_sync",
        "bool",
        True,
        "common",
        "Add the prompts typed in the sessions copied to the VM's prompt history, for the up arrow and Ctrl+R there; "
        "with the sessions only, never twice.",
        agent=True,
    ),
    _client(
        "claude_extensions_sync",
        "bool",
        True,
        "common",
        "Copy your user-level skills, subagents, commands, output styles, workflows, themes and keybindings "
        "(~/.claude/skills/ and the like) to the VM's, but the skills your account syncs, which the VM downloads at "
        "its own sign-in. A file changed on the VM is never overwritten.",
        agent=True,
    ),
    _client(
        "vscode_user_settings_file",
        "path",
        "",
        "common",
        "The VS Code settings read (never written) to say whether the terminal starts on its own. Empty: where stock "
        "VS Code keeps them on macOS and Linux; set it for Insiders, VSCodium, or a VS Code on Windows driven from "
        "WSL. ~ at its start is your home; a relative path is refused by name.",
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
        agent=True,
        values=VERSION_FROM_AN_AGENT,
    ),
    _fleet(
        "claude_code_settings",
        "dict",
        MappingProxyType({}),
        "claude_code",
        "Extra user-level settings merged into ~/.claude/settings.json.",
        secret=True,
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
        agent=True,
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
        agent=True,
    ),
    _fleet(
        "claude_code_remote_control_name",
        "str",
        machine_name,  # computed: the machine's name
        "claude_code",
        "Session title in the list at claude.ai/code; defaults to the VM name.",
        scope="machine",
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
        agent=True,
    ),
    _client(
        "deploy_code",
        "bool",
        True,
        "common",
        "Open the VM in the editor at the end of a one-VM deploy; a deploy of several ends with each VM's line for "
        "code.",
        agent=True,
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
        agent=True,
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
        agent=True,
    ),
    _client(
        "destroy_sessions_dir",
        "path",
        "",
        "common",
        "A folder per VM and run, <name>-<YYYYMMDD-HHMMSS>, 0700. Empty: .sessions/ in this checkout (git-ignored). "
        "The transcripts hold whatever their tools printed, and stay until you delete them. ~ at its start is your "
        "home; a relative path is refused by name.",
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
        agent=True,
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
        scope="machine",
    ),
    _fleet(
        "tailscale_up_args",
        "list[str]",
        (),
        "tailscale",
        "More flags for a join. Never an auth key, which the vault holds: these flags are shown and recorded.",
    ),
    _fleet(
        "tailscale_daemon_flags",
        "list[str]",
        (),
        "tailscale",
        "More flags for tailscaled itself.",
    ),
)


# ---------------------------------------------------------------- the settings the new engine adds ----

# Settings today's example does not document: the new engine's own, the rows that keep today's behaviour where the
# design had none (the owner's answers to O15), the label on a VM, a setting today's vault held that is no secret, and
# two that the repository prints or documents for one run, which are kept in no file.
ADDED: tuple[Row, ...] = (
    # The vault's key.
    _client(
        "vault_key_file",
        "path",
        the_homes_key_file,  # computed: the key file fleet.paths names for the running program's home
        "common",
        "The file that holds the key the vault opens with, and nothing else. Empty: the key file in a folder of this "
        "computer's own, at the top of the checkout's .aw/, beside client.yml, or in the data folder for the installed "
        "program. Never in the fleet's folder, which you copy, nor in the state folder, which a cleanup may remove, "
        "nor in the installed program's config folder, which people publish: a file you name in one of them is "
        "refused by name. Back it up apart from the fleet's folder. ~ at its start is your home; a relative path is "
        "refused by name.",
    ),
    _client(
        "vault_key_command",
        "list[str]",
        (),
        "common",
        "A command of your own whose output is the vault's key, as a list of words run without a shell: a keychain's "
        "or a password manager's reader, or one that asks at each run. It is asked before the file vault_key_file "
        "names. Empty: none.",
    ),
    # This computer's network.
    _client(
        "tls_ca_file",
        "path",
        "",
        "common",
        "A file of PEM certificates added to the list GitHub, Tailscale and the download servers are checked against, "
        "and a Proxmox host that keeps no certificate of its own: a company's root, say. It never replaces that list. "
        "~ at its start is your home; a relative path is refused by name. Empty: none.",
    ),
    _client(
        "network_proxy",
        "str",
        "environment",
        "common",
        "The proxy the calls to GitHub, Tailscale, the download servers and the Proxmox API go through: environment, "
        "the one HTTPS_PROXY or ALL_PROXY names but for a host NO_PROXY names (on macOS, the system's network settings "
        "where the environment names none), as a Python program takes it; none, no proxy whatever the environment "
        "says; or the http:// URL of a proxy, with a host and no user or password: a proxy that needs them is given as "
        "HTTPS_PROXY in the environment, under environment.",
    ),
    # Runs.
    _client(
        "run_lock",
        "str",
        "machine",
        "common",
        "What a run that changes something holds while it runs: machine, each machine it changes, so that runs on "
        "different machines go side by side and one machine is changed by one run at a time; fleet, the whole fleet, "
        "so that one such run runs at a time.",
        choices=("machine", "fleet"),
    ),
    _client(
        "run_forks",
        "int",
        20,
        "common",
        "How many machines one run reaches at once, at least 1: cloned, booted and configured side by side. --set "
        "run_forks=N changes it for one run.",
        least=1,
    ),
    _fleet(
        "run_detached_limit",
        "int",
        120,
        "common",
        "The longest a detached run may last, in minutes: at the limit it stops as a run stops at a Ctrl-C, and its "
        "record says why. 0: no limit.",
    ),
    _fleet(
        "machine_claims",
        "str",
        "inform",
        "common",
        "What a run that changes a machine does where another computer's run is changing it, as the machine's record "
        "says: inform, it goes on, and status shows the other run; refuse, it stands back, and --take-over on that "
        "one command clears a line a computer left behind.",
        choices=("inform", "refuse"),
    ),
    # Agents.
    _fleet(
        "agents_vm_cap",
        "int",
        5,
        "common",
        "How many VMs on a host agents' runs may have made and still have there: a deploy an agent asks for that "
        "would make one more is refused, naming this setting. 0: no cap.",
    ),
    # Proxmox and the VMs.
    _host(
        "proxmox_audit_scope",
        "str",
        "node",
        "common",
        "What the engine's token may read on the host beyond its pool: node, every VM of the node, read only, so that "
        "status shows the node's other VMs and a capacity made from all of them; pool, its pool's alone, and status "
        "then says the others are not read.",
        choices=("node", "pool"),
    ),
    _fleet(
        "vm_label",
        "str",
        "agent-warehouse",
        "common",
        "The label each VM this fleet makes carries in Proxmox's list, so that you see the product's VMs there: one "
        "Proxmox tag. Nothing is decided from it: what is the fleet's is its pool's members with a record.",
        form=PROXMOX_TAG,
    ),
    _fleet(
        "vm_first_boot_upgrade",
        "bool",
        True,
        "common",
        "A new VM upgrades its packages at its first boot, as Proxmox's cloud-init does by default. false skips that "
        "upgrade.",
    ),
    # Destroy.
    _fleet(
        "destroy_confirm",
        "str",
        "yes",
        "common",
        "What destroy asks you to type before it deletes: yes; or names, the names of the VMs it deletes.",
        choices=("yes", "names"),
    ),
    _fleet(
        "destroy_allow_unrecorded",
        "bool",
        False,
        "proxmox_vm",
        "Let destroy delete a member of the fleet's pool that has no record of the fleet's, never a template, nor a VM "
        "another fleet's record names; a VM outside the pool stays out of reach and is refused, naming the pool. Off, "
        "such a VM is refused, so that a name collision cannot destroy a VM this fleet never made.",
    ),
    # GitHub.
    _fleet(
        "github_username",
        "str",
        "",
        "github_projects",
        "The GitHub account whose repositories are listed and cloned onto the VMs, which init writes from its "
        "--github-user. Empty: none given yet.",
        form=GITHUB_USER,
    ),
    # For one run alone.
    _run(
        "proxmox_vm_force_update",
        "bool",
        False,
        "proxmox_vm",
        "Repair VMs that exist, for one run: each VM the run covers has its settings applied again, its size kept as "
        "it was created, and is started, whether or not it was shut down on purpose; where its cloud-init settings "
        "change, its next boot is a new cloud-init instance, which makes new SSH host keys. Kept in no file.",
    ),
    _run(
        "configure_wait_for_cloud_init",
        "bool",
        True,
        "common",
        "Before configuring a VM, wait for cloud-init's first boot to end, and stop where it ended in an error. false, "
        "for one run, configures a VM repaired by hand after such an error. Kept in no file.",
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
        "one ssh picks, from ~/.ssh/config or its own defaults. A key's path is one computer's, kept for each host. ~ "
        "at its start is your home; a relative path is refused by name.",
        scope="host",
    ),
    _client(
        "ssh_executable",
        "path",
        "",
        "connection",
        "The ssh program every connection runs, by its path. Empty: the one found on PATH. ~ at its start is your "
        "home; a relative path is refused by name.",
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

_ALL = ROWS + ADDED + CONNECTION
_BY_NAME = {each.name: each for each in _ALL}
# Each group's name, in the order its first row comes.
GROUPS = tuple(dict.fromkeys(each.group for each in _ALL))


def rows() -> tuple[Row, ...]:
    """Every row: today's documented settings, then the settings the new engine adds, then this computer's own SSH."""
    return _ALL


def row(name: str) -> Row:
    """The row of a setting, by its name; KeyError for a name the table has no row for."""
    return _BY_NAME[name]


def group(name: str) -> tuple[Row, ...]:
    """The rows of a group, in the table's order; KeyError for a name that is no group."""
    if name not in GROUPS:
        raise KeyError(name)
    return tuple(each for each in _ALL if each.group == name)
