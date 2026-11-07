# What the VM trusts

This is a development box, and the defaults reflect that. This page is what they mean for you;
[SECURITY.md](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md) is the policy -
how to report a vulnerability, what is in scope, and the known trade-offs it does not treat as one -
and the [security model](../architecture/security-model.md) is how each is enforced.

- The `dev` user has passwordless sudo and is in the `docker` group, so anything running as that user,
  including Claude Code, is effectively root on the VM. The API key written to `~/.claude/settings.json`
  is therefore readable by any code the agent runs, and so is the GitHub token `gh` keeps in plain text
  in `~/.config/gh/hosts.yml` (mode 0600) - the vault's, or the one a device-flow login issued to that
  VM. For a tighter box, set `common_user_passwordless_sudo: false` and `dev_tools_install_docker: false`
  in `local.yml`, and `vault_vm_password` with `just vault`: sudo then asks for that password, and the
  playbooks become root with it (a run refuses the setting without one). Off, the `common` role takes away
  its own sudo rule and the one cloud-init writes for the user it creates (`NOPASSWD:ALL` in
  `/etc/sudoers.d/90-cloud-init-users`), keeps a later cloud-init run from writing it again, and fails if
  sudo still lets the user run anything without a password. It also takes the user out of the groups
  cloud-init gave it that reach past sudo (`common_user_groups_revoked`): `lxd`, root on a VM that keeps
  snapd (one with a snap installed: LXD comes on first use), `cdrom`, which reads the cloud-init drive
  with the password's hash, and `adm`, every system log; and with Docker off, out of `docker`, which is
  root while Docker is installed. A program already running keeps the groups it started with, until it
  restarts or the VM does. The password is one for every VM, and the playbooks hand it to any VM whose sudo
  asks for it: root on one VM can make its sudo ask, and then holds the sudo password of every other tighter
  box. Make it one you use nowhere else.
- A device-flow login keeps nothing in the vault shared by every VM, but the token it leaves on the VM
  does not expire and carries gh's scopes (`repo`, `read:org`, `gist`) plus `workflow`, broader than a
  fine-grained token limited to chosen repositories with an expiry. It is revoked under GitHub's
  Authorized OAuth Apps, which ends every GitHub CLI sign-in of the account, so `just destroy` leaves
  that to you ([Tearing down](destroy.md)).
- The device-flow code is the VM's to choose: its root decides what `gh` prints there. The page the run opens
  is always GitHub's own, `https://github.com/login/device`, but a code could be one for an OAuth app of the
  VM's choosing, with scopes of its own: before authorising, check that GitHub's page names GitHub CLI. When
  the run says gh on the VM named another page, not GitHub's own, the VM may not be the one you made:
  authorise nothing for it unless you know it is yours.
- Downloads are verified, though not yet everywhere the vendor makes it possible: the uv release by a
  pinned SHA256 and apt repositories by vendored signing keys, but the Ubuntu cloud image only against the
  `SHA256SUMS` from the same server, whose signature (`SHA256SUMS.gpg`) is not checked. The Claude Code
  installer script is fetched over TLS from `claude.ai` and checks the binary against a release manifest from
  the same place; Anthropic signs that manifest (from 2.1.89) and publishes a signed apt repository, but this
  project verifies neither yet, so whoever can serve those addresses can serve the VMs another Claude Code.
  With `dev_tools_install_rust` on,
  rustup comes from Ubuntu's archive, and the Rust toolchain it installs is fetched over TLS from `static.rust-lang.org` and checked against
  the hashes in the channel manifest, which comes from the same place.
- Security updates arrive by two roads. `unattended-upgrades` installs Ubuntu's own security updates every
  day, from Ubuntu's archive only. The packages from the vendors' repositories - Docker, Node.js, the GitHub CLI and
  Tailscale - are upgraded only when a run upgrades packages (`common_apt_upgrade`, on by default: each
  `just deploy` of the VM) or by hand (`sudo apt upgrade`).
- sshd is restricted to key-based logins (`common_harden_ssh`), even if `vault_vm_password` is set.
- tmux on the VM passes escape sequences through to the terminal you attach from (`allow-passthrough`, one
  of the `common_tmux_settings` Claude Code asks for), so a program in a pane can do to that terminal what it
  could do without tmux - set the title, raise a notification, or write the clipboard where the terminal lets
  it. Take `set -g allow-passthrough on` out of `common_tmux_settings`, or set it off in your `~/.tmux.conf`,
  and Claude Code's notifications stay inside tmux.
- `just code` gives each VM this machine's Claude Code sessions of the repositories it has checked out
  ([This machine's Claude sessions on the VM](vscode.md#this-machines-claude-sessions-on-the-vm)): their
  transcripts hold whatever their tools printed, secrets among them, readable there by anything running as
  `dev`. Which sessions go is decided by the GitHub origins of the VM's checkouts, which the VM reports:
  one that makes up a checkout of another of your repositories gets that one's sessions too, so treat a
  VM you open as able to read any of your recent sessions. `claude_sessions_sync: false` stops it.
- `just destroy` brings back what the VM alone holds of them
  ([Its Claude Code sessions](destroy.md#its-claude-code-sessions)) - the VM's own writing, which a VM
  with root can have made up. Only plain files and folders under Claude Code's own names arrive, never a
  link, so nothing on this machine is read or changed through one; what a saved transcript says, and asks
  to run when you resume it, is as trustworthy as the VM was.

## See also

- Settings: [Packages and tools](../reference/settings.md#packages-and-tools) for
  `dev_tools_install_docker`
- Roles: [`common`](../reference/roles/common.md) for `common_user_passwordless_sudo` and
  `common_harden_ssh`, [`dev_tools`](../reference/roles/dev_tools.md) for the downloads it verifies
- How it works: [§10](../architecture/security-model.md); the policy and known trade-offs:
  [SECURITY.md](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md)
