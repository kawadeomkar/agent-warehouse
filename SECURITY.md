# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's ["Report a vulnerability"][advisory] button on
this repository's Security tab, not in a public issue. You should get an acknowledgement within a week.

[advisory]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Scope

This project provisions a VM and installs software on it, so the interesting surface is what it trusts and
what it stores. How each part below is enforced is the
[security model](docs/architecture/security-model.md):

- **The custom modules**: `roles/github_projects/library/github_repos.py`, which handles a GitHub token, and
  the five in `playbooks/library/` that `just code`, `just status` and `just destroy` run, on a VM or on this
  machine - among them `claude_sessions_fetch`, which unpacks here the tar stream a VM sends.
- **Supply chain**: the vendored apt signing keys in `roles/dev_tools/files/` and
  `roles/tailscale/files/`, and every other download a VM gets - the uv release, the Ubuntu cloud image,
  the Claude Code installer - each verified as [What the VM trusts](docs/guides/what-the-vm-trusts.md)
  says.
- **Credential handling**: the Proxmox API token, the GitHub token, the Anthropic API key and the Tailscale
  auth key and API credential, which live in an ansible-vault file, and the Claude sign-in's one-time code.
  Every task that renders one is `no_log`, none is put on a command line or in a file, and the tests grep
  every run for canned ones
  ([The credentials a run handles](docs/architecture/security-model.md#the-credentials-a-run-handles)). What
  `claude`, `gh` and `ssh` print on the VM, which its user controls, reaches this machine only stripped of
  escape sequences and control characters, and a sign-in page opens here only when it is the service's own
  ([What a VM says, quoted here](docs/architecture/security-model.md#what-a-vm-says-quoted-here)). Beyond
  that:

    - The GitHub token is also stored on the VM, by `gh` (`~/.config/gh/hosts.yml`, mode 0600), so that `git`
      and `gh` there can push, and a device-flow login made on the VM stores its own token the same way.
      Either is readable by the VM user, and so by Claude Code
      ([What the VM trusts](docs/guides/what-the-vm-trusts.md)).
    - A run with `ANSIBLE_KEEP_REMOTE_FILES=1`, Ansible's switch for debugging a module, turns pipelining off
      and keeps on the VM the file each module arrives in - the one that carries the Claude sign-in's code
      among them. `claude_code_login_code`, the way a script supplies that code, belongs in a vars file passed
      as `-e @file`: `-e key=value` puts it on this machine's command line for the whole run.
    - The Tailscale auth key (`vault_tailscale_auth_key`) is used for a VM's first join only: every login a
      run sees on the VM's Tailscale - the key's, the web login's, one made by hand - and every sign-in a run
      starts there is recorded as a tag on the VM in Proxmox, which the VM cannot change, and a VM with that
      tag is never sent the key again, so a VM's root cannot log the node out to be handed the fleet's key. A
      login no run has seen, made by hand and logged out before the next run, leaves no record.
    - When `just destroy` releases a VM, gh signs out there and no GitHub token is revoked: revoking one
      GitHub CLI token through GitHub's credential revocation endpoint ended every GitHub CLI sign-in of the
      account, the controller's included, so the report says where to revoke it, and nothing a VM reports
      can get a token revoked.

- **What the VM ends up trusting**: the SSH key material, the passwordless sudo rule and the docker group
  membership. Turned off, the rule goes with the one cloud-init writes and the groups that reach root
  without sudo, and sudo asks for `vault_vm_password`.
- **What is written on the controller**: the four things
  [On the controller](docs/architecture/security-model.md#on-the-controller) lists - `~/.ssh/known_hosts`,
  the managed SSH config `~/.ssh/agent-warehouse.conf` with one `Include` line at the top of `~/.ssh/config`,
  the checkout's own folder outside it (`tools/env-run --data`), and VS Code's Remote - SSH extension, which
  `just code` has VS Code's own CLI install from VS Code's marketplace when it is missing, and no other
  extension or VS Code setting. A bug in the SSH config reaches every `ssh` on the user's machine, and the VM
  chooses what goes in it: its address, which its own guest agent reports, and its name, which is Proxmox's;
  that section says how each is held. Inside the checkout, `.logs/` holds a copy of every Ansible run: `no_log`
  output is hidden there as on screen, but the files name hosts and addresses, so the directory is
  git-ignored, blocked by `tests/check_no_local_files.sh`, created `0700`, and pruned after
  `LOG_RETENTION_DAYS`. A secret that appears in a log is a `no_log` that is missing, and in scope.

Findings in any of those are in scope, as is anything that would cause a secret to be written to a log or
the repository.

## Known trade-offs, not vulnerabilities

Some defaults trade strictness for a working first run. These are documented in the wiki - the defaults
a VM lives with in [What the VM trusts](docs/guides/what-the-vm-trusts.md), how each is enforced in
[docs/architecture/security-model.md](docs/architecture/security-model.md) - and reports that only
restate them are not treated as vulnerabilities:

- `proxmox_validate_certs` defaults to `false`, because a stock Proxmox install presents a self-signed
  certificate, so every run sends the API token to whatever answers at the host's address on port 8006.
  [Proxmox access](docs/getting-started/proxmox-access.md#trusting-the-proxmox-certificate)
  explains how to trust the Proxmox CA and turn it on; a later release will have `just init` fetch the CA
  and turn validation on by default. Until you turn it on, count the project's own VMs among the devices
  that could answer there. They sit on the bridge you choose (`proxmox_bridge`), by default `vmbr0`, the
  bridge the host's own address is usually on, with no Proxmox firewall on their network device. So that
  network always has a device you cannot fully trust on it: root on any VM - an agent with passwordless
  sudo, or another user's VM on a shared host - can try to answer for the host's address and read the
  token, which reaches every VM on the host (below).
- **The API token's reach.** `tools/proxmox-access.sh` grants the `AnsibleVM` role on `/vms`, and the grant
  propagates to every VM on the host. The token can set the SSH keys cloud-init installs on any VM there,
  restart it into them, and delete it - this project's VMs or not - and so can anyone who obtains the token,
  by answering for the host (above) or from the vault. The ownership tag keeps this project's own runs off
  other VMs; it does not limit the token. A later release will scope the grant to a Proxmox pool. Until
  then, on a host that also runs VMs you care about, guard the vault and `.vault_pass` as you would a root
  password for those VMs.
- **A shared Proxmox host.** The ownership tag (`claude_vm_tag`, `agent-warehouse` by default) is a
  convention, not a permission: any user of the host who can create VMs can set it, and your token can list
  every VM on the host (above). Every tagged VM your token lists is treated as yours:

    - A fleet-wide `just deploy --only configure` reaches it and, where it accepts your SSH key - its root
      decides that - configures it with your secrets: the GitHub token, the Anthropic API key, the
      Tailscale auth key.
    - `just deploy <name>`, for a name only their tagged VM has, configures and signs in that VM rather
      than creating yours.
    - `just destroy <name>`, for a name only their tagged VM has, deletes it. Deletion goes through the
      API and needs no SSH.

  On a shared host, give each person a tag of their own in `local.yml` (`claude_vm_tag`) before their
  first deploy - VMs made under one tag are not found under another, and a Proxmox
  [User Tag Access](docs/getting-started/proxmox-access.md#user-tag-access) list must allow it and its
  `-unfinished` and `-tailscale` forms - and name the VMs a run is for. That keeps each person's runs off the others' VMs by
  accident, but not off a VM someone made to look like yours. A token scoped to a Proxmox pool of each
  person's own, which cannot list the others' VMs at all, is planned.
- The VM's user gets passwordless sudo and docker group membership by default; both are toggles, and
  [What the VM trusts](docs/guides/what-the-vm-trusts.md) says what they mean.
- `vault_vm_password`, when set, is one password for the VM user on every VM, for the Proxmox console and
  for sudo with `common_user_passwordless_sudo: false`. SSH never takes it. Each VM holds its hash (in
  `/etc/shadow` and on its cloud-init drive), and the playbooks become root with it whenever sudo asks, so
  root on one VM can make its sudo ask and read the password from the next run. With it, that VM's root
  has the password to every other VM's console and, on the tighter box, to every other VM's sudo. Make it
  one you use nowhere else; a password of each VM's own is not built yet.
- SSH host keys are accepted on first contact (`StrictHostKeyChecking=accept-new`) so a freshly cloned VM
  can be reached without manual steps. Changed keys are still refused.
- The `vscode_server` role keeps `AllowTcpForwarding` on, which VS Code's Remote - SSH, Claude's sign-in in the
  browser and Zed's port forwards use, and OpenSSH defaults to anyway; the VM user can forward ports, as a user with passwordless sudo always could.
  SSH agent forwarding to the VM is off by default and documented as a choice, because the VM user is
  root-equivalent and runs an AI agent.
- In VS Code, `just code` opens a workspace file whose tasks start `claude` in a terminal, and a shell beside
  it, when the window opens (`editor_claude_terminal`, on by default). The file is written by this project
  beside the repositories, never in one, and what its tasks run is fixed
  ([In the editor](docs/architecture/security-model.md#in-the-editor)). VS Code still runs
  it only in a window you have trusted and once you have allowed automatic tasks; the project reads
  that setting to explain what will happen, and never writes it, sets trust, or relaxes either check.
  Allowing automatic tasks is a VS Code-wide choice: it also lets a trusted repository's own
  `folderOpen` tasks run, as it would without this project.
- The same file's "Session notes" task writes a page about the VM next to it (`editor_session_notes`,
  on by default): its addresses, and the Claude, GitHub and Tailscale accounts signed in there, read
  from the VM as the window opens. It is the VM user's alone (0600, in a 0700 directory), never in a
  repository; no command the task runs prints a token, and what the VM reports is stripped of escape
  sequences and control characters before it is written. A window it would put back in Restricted
  Mode never gets the notes' folder: the project does not change what a window you trusted covers. In
  Zed, `just code` alone writes the page, and nothing rereads its sign-ins as the window opens.
- `just code` copies this machine's Claude Code sessions of the last 30 days to the VM it opens
  (`claude_sessions_sync`, on by default): the transcripts of the repositories that VM has checked
  out, matched by git origin. A transcript holds whatever its tools printed - command output, the
  contents of files, tokens - so every VM you open gets those of its repositories' sessions, in the VM
  user's `~/.claude` (0600 files, 0700 folders), never in a repository, and as rsync's unedited mirror of
  them in `just code`'s own directory there (`<projects>/.agent-warehouse/claude-sessions/`, 0700);
  `false` keeps them here from then on, and leaves what is on the VM already - mirror and copies - for you
  to delete, or for `just destroy` to take with the VM. VS Code's window hides that directory; Zed's shows
  it, in its project panel, search and the context of Zed's own AI features, unless you exclude it there
  (`docs/guides/zed.md`). `just code` copies nothing back, the copy's own tasks print nothing a transcript
  holds, and a session continued on the VM is never overwritten. A VM chooses which of this machine's
  sessions it gets through the GitHub origins of its checkouts, and only those. Here, though, a checkout's
  origin counts by its owner and name whatever its host, so a VM whose checkout names
  `github.com/team/internal` also gets the sessions of a checkout here of `team/internal` on GitLab or a
  server of your own, and a VM whose root is not yours can name every repository your GitHub token lists.
  `--session <id>` moves the VM's checkout of that session's repository to the commit it is pushed at, by
  fast-forward only: never over changes of the VM's own, an ignored file the commit would replace, or
  commits a detached `HEAD` alone holds, and never while a `claude` works in it. Claude Code's question
  whether to trust that folder is left for you to answer.
- `just code` refuses to write its files inside a checkout of the projects directory, for every editor, but
  looks nowhere else: not outside the projects directory, where `editor_files_dir` may point, not in a
  projects directory that is itself a checkout, and not where a symlink on the way leads
  ([In the editor](docs/architecture/security-model.md#in-the-editor)). Keep `editor_files_dir` out of every
  repository.
- With `editor: zed`, `just code` writes two files for Zed in its own directory on the VM - a
  **Claude (just code)** task of fixed text with no tag, and the helper it runs - and no Zed setting, keymap,
  task or trust of yours; no task starts on its own, and you start that one, or `claude` in Zed's terminal,
  which is the VM's ([In the editor](docs/architecture/security-model.md#in-the-editor)). Zed lists a folder's
  tasks in Restricted Mode too (seen on Zed 1.26.2), your repositories' own beside it, and trusting the
  projects directory's window trusts every checkout in it, so do not trust it for the Claude task alone. The
  project binds no key and publishes no tag, which any repository could put on a task of its own; the key
  `docs/guides/zed.md` shows is yours, with a tag of yours, and reaches every window. Zed's Agent Panel runs
  its Claude Agent on the VM as well, as the VM's user, with the VM's `~/.claude` and claude.ai login, so its
  tools have the VM's files in reach and not this machine's (seen on Zed 1.26.2; Zed's documentation does not
  say). Its Claude Code is the one its npm package bundles, at the version Zed's registry installs, not the one
  `just deploy` keeps current. Zed's remote server, in the VM's `~/.zed_server`, and that agent, in
  `~/.local/share/zed/`, are the VM user's to replace, as VS Code Server's is
  ([What Zed decides](docs/guides/zed.md#what-zed-decides)).
- `just destroy` saves the Claude Code sessions and auto memory that exist only on a VM into
  `.sessions/` in your checkout before deleting it (`destroy_save_sessions`, on by default): plaintext
  transcripts holding whatever their tools printed, in 0600 files and 0700 folders readable by you alone,
  ignored by git and refused by the tracked-file guard. They stay until you delete them; `false` deletes
  them with the VM. What the VM sends is not trusted, since a VM's root can make up anything: only plain
  files and folders under the names Claude Code gives its own are written, never a link and never outside
  that folder, and nothing on this machine is read or changed through one. A saved transcript is still the
  VM's writing - treat what it says, and what resuming it here asks to run, as you would the VM itself.
- Deleting a VM also removes its host key from `known_hosts`, which re-opens that first-contact window
  for its address. The address goes back into the DHCP pool, so whatever answers there next is trusted
  once - and the next `just deploy` then sends it your SSH keys, GitHub token and Anthropic API key. The
  sign-ins hand it more: `just deploy` and `just login` run `claude auth login` and `gh auth login`
  there and ask you to authorise what they print, so an impostor that prints
  a genuine sign-in page or device code of its own receives a claude.ai login, or a GitHub OAuth token
  with gh's scopes (`repo`, `read:org`, `gist`) and `workflow` that does not expire until revoked - with
  no GitHub token in the vault as much as with one. Authorise only a page or code for a VM you have just
  created or know to be yours; a run that says gh on the VM named another page, not GitHub's own, is one
  more sign that the VM is not. Keeping
  the key instead would only trade this for a failure every time a lease is recycled, so the removal is
  deliberate. On a network where you do not trust every device, put the VMs on a bridge that only
  trusted devices can reach (`proxmox_bridge`, set before `just deploy --only template`, since every clone inherits
  the template's NIC). A fixed address from `vm_ipconfig`, outside the DHCP pool, keeps the
  address from being leased to anything else, but only for a single VM: that one value applies to every
  VM a run provisions, so a second VM would be given the same address.

- **Tailscale reach.** A tailnet's default policy lets every device reach every other, tagged devices
  included, and these VMs run an AI agent with passwordless sudo. The [policy edit](docs/guides/tailscale.md#keeping-an-agent-vm-off-your-other-devices) narrows the
  default rule to the people's own devices, so a tagged VM can be reached but can open no connection, and
  adds a test the admin console runs on every save; a VM joined by the web login without a tag is the
  user's own device, and reaches what the user's devices reach. On the VM, sudo can always log the node
  out, use `tailscale serve` or `funnel` where the policy allows them, or read
  `/var/lib/tailscale/tailscaled.state`: only the tag and the policy bound what the node can reach. By
  default the VM keeps its own resolver (`tailscale_accept_dns: false`): a tailnet's DNS settings that
  override local DNS would otherwise answer every lookup on the VM. Tailscale's log upload is on, as
  Tailscale ships it; `tailscale_upload_logs: false` turns it off.
- **What a destroyed VM tells the controller.** `just destroy` releases a VM before deleting it, from
  what the VM reports - and the VM runs an agent with sudo. The Tailscale node ID the device is deleted by
  is the VM's to report, and the API credential can delete any device in the tailnet, so the device is
  looked up first and deleted only when its name is the one this project asked the VM to join as
  (`tailscale_hostname`, or that name with Tailscale's `-<n>`); anything else is said, not deleted. A VM
  whose address was recycled since is released wherever that address
  now answers - an SSH host key it does not hold fails the connection first, and nothing is sent to it but
  the release's own commands.
- **The Claude sign-in's forward.** While Claude's browser sign-in waits, one port on this machine's
  loopback, on both its addresses (`ssh -L 127.0.0.1:<port>:… -L [::1]:<port>:…`), reaches claude's callback
  on the VM's own `127.0.0.1`, so any process on this machine can reach that callback for those minutes - as
  any process on the VM always can. The port is the VM's to choose, read from the page its claude handed
  its browser: it must be between 1024 and 65535, the forward fails rather than share a port already in use
  here on either address, since a browser tries both for `localhost` (so a machine with no IPv6 loopback
  always pastes the code instead), and its far end ends with the sign-in - or, after a run stopped while it
  waited, with the next sign-in of the VM from this machine. On macOS, though, the forward can
  bind the port beside a program of yours that listens on every address, and then takes that program's
  connections over loopback at that port to the VM for those minutes; a program of another user's, root's
  included, refuses the bind, and the forward fails. The redirect, a cross-site top-level
  request from Claude's site to `localhost`, carries the browser's `localhost` cookies that are not
  `SameSite=Strict` and whose `Path` matches `/callback`, whichever local web app set them on whichever
  port, to whatever listens on the VM's port - claude, or anything the VM's root puts there; the project
  cannot strip them, because Claude Code fixes the `localhost` redirect. A run stopped while it waits
  leaves on the VM a record of which run it was, which the VM can read and write anything over: the next
  sign-in reads it back as untrusted, quotes only a host name and a pid from it, and decides from it only
  whether that attempt's own processes on the VM are ended
  ([The browser sign-in](docs/architecture/roles.md#the-browser-sign-in)). The page opens here only when it is
  Claude's own sign-in page; the recycled-address trade-off above applies to it as to the code it replaces.
- **A pending web login.** While Tailscale's web login waits for an approval, its page is readable by
  every local user on the VM (`tailscale status --json`), and whoever approves it first decides which
  tailnet the VM joins. Each report names the tailnet joined, so one that is not yours shows; the key path
  has no page at all. The page is opened on this machine only when it is Tailscale's own sign-in page.
  The recycled-address trade-off above extends to it: an impostor's page, approved, joins the impostor to
  your tailnet.

If you can show one of these is exploitable beyond the documented trade-off, please report it.

## Secrets committed by mistake

CI runs gitleaks over the working tree and over every commit a push or pull request brings in, so a
token committed and then deleted in a later commit still fails the run
([docs/architecture/security-model.md](docs/architecture/security-model.md#secret-scanning) lists what
it cannot see). A failing scan means the secret has already been pushed to a public repository, and
rewritten commits stay reachable by SHA, from pull requests and in forks. Revoke or rotate the
credential first, as GitHub's guide to [removing sensitive data][sensitive] advises; rewriting history
comes second.

[sensitive]: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
