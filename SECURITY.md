# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's ["Report a vulnerability"][advisory] button on
this repository's Security tab, not in a public issue. You should get an acknowledgement within a week.

[advisory]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Scope

This project provisions a VM and installs software on it, so the interesting surface is what it trusts and
what it stores:

- **The custom module** `roles/github_projects/library/github_repos.py`, which handles a GitHub token.
- **Supply chain**: the vendored apt signing keys in `roles/dev_tools/files/` and
  `roles/tailscale/files/`, and every other download a VM gets - the uv release, the Ubuntu cloud image,
  the Claude Code installer - each verified as [What the VM trusts](docs/guides/what-the-vm-trusts.md)
  says.
- **Credential handling**: the Proxmox API token, GitHub token and Anthropic API key, which live in an
  ansible-vault file and are marked `no_log`. Every task that hands the GitHub token on is `no_log` too,
  which also keeps it out of the VM's journal (a module logs the arguments its spec does not mark
  `no_log`, stdin included), and `tests/unit/test_github_auth.py` holds each to it and greps every run,
  module arguments included, for every canned token; `github_repos` strips the token and masks the
  stripped form as well. What `claude`, `gh` and `ssh` print on the VM, which its user controls, is
  quoted in messages only through the `plain_text` filter, with every escape sequence and control
  character removed; so are the warnings, deprecations and failure messages any module there returns,
  which ansible-core prints as they are, and a dry run's diffs: the stdout callbacks print them through it
  (`filter_plugins/text.py`'s `plain_summary`). NEXT STEPS takes its lines from the project's own
  `set_stats` tasks only, and `just status` its report from its own play on this machine, since any
  module's result can carry custom stats and facts. GitHub's device flow opens and shows GitHub's own
  page, `https://github.com/login/device`, whatever gh printed: a page gh prints that
  `github_projects_login_page_pattern` does not match is said to be another, and is neither opened nor
  quoted - where a failure quotes gh's own lines, `<a page that is not GitHub's own>` stands in its place;
  Claude's and Tailscale's pages likewise open only when they are their own. No page opens through a
  `BROWSER` that is a shell or a Windows command interpreter, which would read the page's query string as
  a command line. The GitHub token is also stored on the VM, by `gh`
  (`~/.config/gh/hosts.yml`, mode 0600), so that `git` and `gh` there can push; a device-flow login
  made on the VM stores its own token the same way. Either is readable by the VM user, and so by
  Claude Code ([What the VM trusts](docs/guides/what-the-vm-trusts.md)). The Claude sign-in's one-time code
  (`roles/claude_code/tasks/browser_login.yml`) is read at a `pause` under `no_log` and travels to the
  VM on stdin into a 0600 named pipe only that `claude auth login` reads. With pipelining on, as
  `ansible.cfg` sets it, it is never written to a file or a command line; with pipelining off - which
  `ANSIBLE_KEEP_REMOTE_FILES=1`, Ansible's switch for debugging a module, forces - the module that
  carries it is a file on the VM, and that switch keeps the file. `claude_code_login_code`, the way a
  script supplies the code, belongs in a vars file passed as `-e @file`: `-e key=value` puts it on this
  machine's command line for the whole run. `tests/unit/test_claude_login.py` holds both prompts and both
  writers to `no_log` and greps whole runs for the code. The login it
  produces is Claude Code's own `~/.claude/.credentials.json`, mode 0600, which the project never reads.
  The Tailscale auth key (`vault_tailscale_auth_key`) is used for a VM's first join only: every login a
  run sees on the VM's Tailscale - the key's, the web login's, one made by hand - and every sign-in a run
  starts there is recorded as a tag on the VM in Proxmox, which the VM cannot change, and a VM with that tag
  is never sent the key again, so a VM's root cannot log the node out to be handed the fleet's key. A login
  no run has seen, made by hand and logged out before the next run, leaves no record. It goes to the
  VM the same way: `tailscale up --auth-key=file:/dev/stdin`, the key as the `command` module's stdin
  under `no_log`, so it is on no command line and in no file, and never stored on the VM - tailscaled
  keeps the node's own keys, not the auth key. `tests/unit/test_tailscale.py` holds that task to be the
  only one that renders the key, and greps every run, module arguments included, and every file, for every
  canned key; the role's Molecule scenario searches the VM for it after the join.
  The Tailscale API credential that deletes a VM's device (`vault_tailscale_api_token`, or
  `vault_tailscale_oauth_client_id` and `_secret`) is used only on this machine, in `uri` calls under
  `no_log`, and never reaches a VM. When `just destroy` releases a VM, gh signs out there and no GitHub
  token is revoked: revoking one GitHub CLI token through GitHub's credential revocation endpoint ended
  every GitHub CLI sign-in of the account, the controller's included, so the report says where to revoke
  it. `tests/unit/test_release.py` holds that nothing reaches that endpoint, and greps the release's whole
  output, module arguments included, for every canned credential.
- **What the VM ends up trusting**: the SSH key material, the passwordless sudo rule and the docker group
  membership. Turned off, the rule goes with the one cloud-init writes and the groups that reach root
  without sudo, and sudo asks for `vault_vm_password`.
- **What is written on the controller**: `~/.ssh/known_hosts`, the managed SSH config
  `~/.ssh/claude-on-proxmox.conf` and the one `Include` line at the top of `~/.ssh/config`
  (`playbooks/tasks/ssh_config.yml`). A bug there reaches every `ssh` on the user's machine. The VM's
  address in it is what the VM's own guest agent reported, so the VM chooses it: the `guest_ipv4`
  filter takes only a string that parses as one IPv4 address, and the task file refuses a name or
  address that is not one plain word, since a newline in either would add a directive (`ProxyCommand`
  runs on this machine). The VM's name is Proxmox's, chosen by whoever made it, and the managed file is
  included ahead of everything in `~/.ssh/config`: a name ssh would take for another host (one it reads
  as an address; a name of a host of the inventory, or the address it is reached at) gets no alias -
  refused when named, left out of a fleet-wide refresh and said - and a name with a dot, as in
  `github.com`, gets one only when the user names it, never from a fleet-wide refresh. A VM named after a host of the inventory is kept
  out wherever a name becomes a host (`localhost`, or the Proxmox host's, which would hand the VM the
  API token).
  One more write is VS Code's own, at the project's request: when VS Code lacks the Remote - SSH
  extension (`ms-vscode-remote.remote-ssh`), which `just code` needs to open a VM, `just code` and
  `just deploy`'s window step run `code --install-extension` for it, and VS Code's CLI installs it from
  VS Code's marketplace into VS Code's extensions folder; no other extension, and no VS Code setting.
  Inside the checkout, `.logs/` holds a copy of every Ansible run: `no_log` output is hidden there
  as on screen, but the files name hosts and addresses, so the directory is git-ignored, blocked by
  `tests/check_no_local_files.sh`, created `0700`, and pruned after `LOG_RETENTION_DAYS`. A secret
  that appears in a log is a `no_log` that is missing, and in scope.

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
- **A shared Proxmox host.** The ownership tag (`claude_vm_tag`, `claude-on-proxmox` by default) is a
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
- The `vscode_server` role keeps `AllowTcpForwarding` on, which VS Code's Remote - SSH needs and OpenSSH
  defaults to anyway; the VM user can forward ports, as a user with passwordless sudo always could.
  SSH agent forwarding to the VM is off by default and documented as a choice, because the VM user is
  root-equivalent and runs an AI agent.
- `just code` opens a workspace file whose tasks start `claude` in a terminal, and a shell beside it, when the window opens
  (`vscode_claude_terminal`, on by default). The file is written by this project beside the
  repositories, never in one, and its command is fixed in `playbooks/code.yml`. VS Code still runs
  it only in a window you have trusted and once you have allowed automatic tasks; the project reads
  that setting to explain what will happen, and never writes it, sets trust, or relaxes either check.
  Allowing automatic tasks is a VS Code-wide choice: it also lets a trusted repository's own
  `folderOpen` tasks run, as it would without this project.
- The same file's "Session notes" task writes a page about the VM next to it (`vscode_session_notes`,
  on by default): its addresses, and the Claude, GitHub and Tailscale accounts signed in there, read
  from the VM as the window opens. It is the VM user's alone (0600, in a 0700 directory), never in a
  repository; no command the task runs prints a token, and what the VM reports is stripped of escape
  sequences and control characters before it is written. A window it would put back in Restricted
  Mode never gets the notes' folder: the project does not change what a window you trusted covers.
- `just code` copies this machine's Claude Code sessions of the last 30 days to the VM it opens
  (`vscode_claude_sessions`, on by default): the transcripts of the repositories that VM has checked
  out, matched by git origin. A transcript holds whatever its tools printed - command output, the
  contents of files, tokens - so every VM you open gets those of its repositories' sessions, in the VM
  user's `~/.claude` (0600 files, 0700 folders), never in a repository, and as rsync's unedited mirror of
  them in `just code`'s own directory there (`<projects>/.claude-on-proxmox/claude-sessions/`, 0700);
  `false` keeps them here from then on, and leaves what is on the VM already - mirror and copies - for you
  to delete, or for `just destroy` to take with the VM.
  `just code` copies nothing back, the copy's own tasks print nothing a transcript holds, and a session
  continued on the VM is never overwritten. `--session <id>` moves the VM's checkout of that session's
  repository to the commit it is pushed at, by fast-forward only: never over changes of the VM's own, an
  ignored file the commit would replace, or commits a detached `HEAD` alone holds, and never while a
  `claude` works in it. A VM chooses which of this machine's sessions it gets through the GitHub origins
  of its checkouts, and only those: a local path or another host's URL there matches none. Here, though, a
  checkout's origin counts by its owner and name whatever its host, so a VM whose checkout names
  `github.com/team/internal` also gets the sessions of a checkout here of `team/internal` on GitLab or a
  server of your own, and a VM whose root is not yours can name every repository your GitHub token lists.
  Claude Code's question whether to trust that folder is left for you to answer.
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
  (`tailscale_hostname`, or that name with Tailscale's `-<n>`); anything else is said, not deleted. No
  GitHub token is revoked, so nothing the VM reports can get one revoked. A VM whose address was recycled
  since is released wherever that address
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
  cannot strip them, because Claude Code fixes the `localhost` redirect. To tell a stopped run gone, each
  attempt records on the VM which run waits for it: this machine's name, this start of it (Linux's boot
  ID, or when PID 1 started) and the run's pid and start. The VM can read that record and write anything
  over it, so it is read back as untrusted: only a host name and a pid are ever quoted from it, the check
  is a read of this machine's process table, and what it decides is only whether the VM's own processes of
  that attempt are ended. The page opens here only when it is Claude's own sign-in page; the
  recycled-address trade-off above applies to it as to the code it replaces.
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
