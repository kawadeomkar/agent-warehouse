"""What the new engine writes on the person's computer outside the checkout: one row a place, in the form of today's
playbooks/vars/controller_writes.yml (card G8; docs/architecture/engine.md §16.4).

Each row says where (`path`, as a person reads it, `~/...`, or by the function of fleet/paths.py that gives it:
`where`), what is there, who writes it, when, what takes it out, and its sources: the files of the new engine that make
the write. A row whose write no card has built yet names its card (`card`) and no source.
tests/unit/test_aw_controller_writes.py finds each row's write where the engine makes it, and fails on a file of the new
engine that writes and is the source of no row; a card that adds a write on the computer outside the checkout adds its
row here in the same commit. Today's list and its test hold today's product until the cutover; K13 writes the security
model's table from this one at the flip.

Data alone: nothing is read or written here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from ..fleet import paths


@dataclasses.dataclass(frozen=True)
class Row:
    """One place the engine writes on the computer: its id, where it is, what it holds, who writes it, when, what takes
    it out, the files of the new engine that write it (by their path in the checkout), the function of fleet/paths.py
    that gives the place where one does, the setting that moves or turns it off where one does, and the card that
    builds a write not built yet."""

    id: str
    path: str
    what: str
    writer: str
    when: str
    removed_by: str
    sources: tuple[str, ...] = ()
    where: Callable[..., object] | None = dataclasses.field(default=None, compare=False)
    setting: str = ""
    card: str = ""


ENGINE = "lib/claude_vm/engine/"
SSH = "lib/claude_vm/ssh/"
FLEET = "lib/claude_vm/fleet/"
RUNS = "lib/claude_vm/runs/"
GUEST = "lib/claude_vm/guest/"

ROWS: tuple[Row, ...] = (
    Row(
        id="known_hosts",
        path="`~/.ssh/known_hosts`",
        what="the Proxmox host's SSH host key - never a VM's",
        writer=(
            "your own ssh, on the first SSH to the host, which takes the key at first use (accept-new): root SSH to the"
            " host, or the hop's login through it to the VMs (`vm_ssh_route`)"
        ),
        when="init's checks, a template build, and every run whose route to the VMs is the Proxmox host",
        removed_by="nothing: it is yours",
        sources=(f"{SSH}runner.py",),
    ),
    Row(
        id="vm_known_hosts",
        path="`~/.ssh/agent-warehouse.known_hosts`",
        what="each VM's SSH host key, under the VM's identity (`agent-warehouse-<SMBIOS UUID>`), never its address",
        writer=(
            "the engine, once a VM's first connection took its key into a scratch file of the run's own: one line"
            " appended under a short lock, never a line replaced (ssh/hostkeys.py's `file`); and your own ssh through"
            " an alias, which takes a key at first use under the VM's identity alone"
        ),
        when="every run that makes or first reaches a VM, and `ssh <vm>` through its alias",
        removed_by=(
            "nothing: a deleted VM's key stays under its identity, which no VM made later has, so that any VM given it"
            " again is refused, never taken on first use. `ssh-keygen -R <identity> -f"
            " ~/.ssh/agent-warehouse.known_hosts` takes one out"
        ),
        sources=(f"{SSH}hostkeys.py", f"{ENGINE}aliases.py"),
    ),
    Row(
        id="ssh_config",
        path="`~/.ssh/agent-warehouse.conf`",
        what="one `Host` block per VM, so that `ssh <vm>` and the editor reach it, recording the host the VM is on",
        writer="`aw deploy --only ssh-config` (engine/aliases.py's refresh), and `aw destroy`, which removes one",
        when="`claude_ssh_config` on (the default)",
        removed_by=(
            "`aw deploy --only ssh-config` prunes a VM that is gone from its host; `aw destroy` the VM's, unless its"
            " block records another host"
        ),
        sources=(f"{ENGINE}aliases.py",),
        setting="claude_ssh_config",
    ),
    Row(
        id="ssh_include",
        path="one `Include` line at the top of `~/.ssh/config`",
        what="makes ssh read the file above",
        writer="the same, once: the rest of the file as it was, its mode kept",
        when="`claude_ssh_config_include` on, while ssh does not reach the file yet",
        removed_by="nothing: it is yours to remove",
        sources=(f"{ENGINE}aliases.py",),
        setting="claude_ssh_config_include",
    ),
    Row(
        id="data",
        path="the checkout's own Python environment, in `~/.local/share/agent-warehouse/checkouts/<folder>-<checksum>`"
        " (under `$XDG_DATA_HOME` where it is set)",
        what="this checkout's Python environment, where you bring no virtualenv of your own",
        writer="the launcher, `aw install` (`aw`'s install_env, through uv)",
        when="the first run of a checkout with no environment, and `aw install`",
        removed_by="you: remove the folder",
        sources=("aw",),
    ),
    Row(
        id="vscode_extension",
        path="VS Code's Remote - SSH extension, in `~/.vscode/extensions/`",
        what="Microsoft's extension, from VS Code's marketplace",
        writer="VS Code's own command line, when `aw code` finds it missing",
        when="`editor: vscode`",
        removed_by="VS Code",
        card="G17",
    ),
    Row(
        id="editor_records",
        path="your editor's own records of the window",
        what="its recent windows and its remote connections' state, in its own data folder",
        writer="the editor itself, when `aw code` opens a window",
        when="every window",
        removed_by="the editor",
        card="G17",
    ),
    Row(
        id="mcp",
        path="the `agent-warehouse` MCP server's entry in `~/.claude.json`",
        what="what lets Claude Code sessions run these commands",
        writer="Claude Code's own command line, when `aw mcp` runs it",
        when="only after `aw mcp`",
        removed_by="`aw mcp`'s removal, through the same command line",
        card="I6",
    ),
    Row(
        id="sessions_dir",
        path="`destroy_sessions_dir`",
        what="the Claude Code sessions `aw destroy` saved from a VM",
        writer="`aw destroy`",
        when="only where you point it outside the checkout",
        removed_by="you",
        setting="destroy_sessions_dir",
        card="G13",
    ),
    Row(
        id="fleet",
        path="the fleet's home (fleet/paths.py's `home`): its `client.yml`, this computer's own, and its fleet folder:"
        " each host's folder (its `host.yml` and the API's certificate anchor), `settings.yml`, `machines.yml`, the"
        " vault and the fleet's mark",
        what="the fleet as `init` makes it and the settings, hosts and vault commands change it",
        writer="`aw init`, `aw host`, `aw settings` and `aw vault`",
        when="each of those runs",
        removed_by="you: the folder is yours, and the fleet folder the one you copy to a second computer",
        sources=(f"{FLEET}files.py", f"{FLEET}hosts.py", f"{FLEET}overrides.py"),
        where=paths.fleet_dir,
    ),
    Row(
        id="vault_key",
        path="the vault's key file (fleet/paths.py's `key_file`): at the top of the home, or the data folder's for the"
        " installed program; in neither the fleet folder nor the state folder",
        what="the key the fleet's vault is sealed to, made once and never replaced",
        writer="`aw init`, the first time (fleet/vaultkey.py)",
        when="once",
        removed_by="nothing: without it the vault cannot be opened",
        sources=(f"{FLEET}vaultkey.py",),
        where=paths.key_file,
    ),
    Row(
        id="jobs",
        path="the state folder's job folders (fleet/paths.py's `jobs_dir`)",
        what="each run's record, its lock and its log, which `aw logs` and `aw status` read",
        writer="every run (runs/job.py)",
        when="every run",
        removed_by="a run refused at its locks drops its own; you may remove the folder of a run that ended",
        sources=(f"{RUNS}job.py",),
        where=paths.jobs_dir,
    ),
    Row(
        id="locks",
        path="the state folder's lock files (fleet/paths.py's `locks_dir`)",
        what="one empty file a lock - the fleet's, each machine's, the short sections' - held by the kernel",
        writer="every run that takes a lock (runs/locks.py)",
        when="every run but the ones that take none",
        removed_by="nothing: empty, and released when the run ends",
        sources=(f"{RUNS}locks.py",),
        where=paths.locks_dir,
    ),
    Row(
        id="run_dir",
        path="a run's own short-lived directory, `aw-run-<job>-<random>`, in the per-user temporary directory",
        what=(
            "what one run hands its children: layer 1's configuration and variables, a first connection's scratch"
            " known-hosts file, and the aliases' text while ssh validates it"
        ),
        writer="every run (runs/rundir.py), and those of its steps",
        when="every run",
        removed_by="the run, at its end",
        sources=(
            f"{RUNS}rundir.py",
            f"{GUEST}layer1.py",
            f"{GUEST}variables.py",
            f"{SSH}hostkeys.py",
            f"{ENGINE}aliases.py",
        ),
    ),
    Row(
        id="control_dir",
        path="Ansible's own `~/.ansible/cp`",
        what=(
            "the SSH connection sockets a run shares, the one through the Proxmox host among them where the VMs are"
            " reached through it: Ansible's own folder, which today's runs write already"
        ),
        writer="every run that reaches a machine (ssh/connections.py)",
        when="every run that reaches a machine",
        removed_by="each socket goes with its connection",
        sources=(f"{SSH}connections.py",),
        where=paths.control_dir,
    ),
)

# What else the computer holds of a run's, which no row is: other programs' own folders.
ALSO = (
    "uv's cache, `~/.cache/uv`, and the Python it downloads for the checkout's own environment, in"
    " `~/.local/share/uv/python`.",
    "Not files: the sign-in pages opened in your browser, and GitHub's device code put on the clipboard.",
)
