# VS Code

You use VS Code on a VM through its [Remote - SSH](https://code.visualstudio.com/docs/remote/ssh)
extension, which runs the editor's back end on the VM, over the same key-only SSH everything else
uses: the window is on your machine, the files, the terminal, the extensions and `claude` are on the
VM. Nothing new is opened, relayed or logged into. The one thing it needs that `ssh dev@<address>`
does not have is a name that follows the address DHCP gave the VM, and that is what this project
keeps for you.

VS Code is the editor `just code` and `just deploy` open by default. The `editor` setting picks the one they
open, one at a time (`just settings editor`), and `just code alpha --editor <editor>` picks it for one run;
the other is [Zed](zed.md).

## What a window on a VM trusts

A window on a VM trusts that VM with this machine. VS Code's server runs on the VM and the window here does
what it says, and Microsoft's note for Remote - SSH is plain: "A compromised remote could use the VS Code
Remote connection to execute code on your local machine" ([Remote - SSH](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh)). No setting, profile or
Workspace Trust changes that. The VM runs an agent with passwordless sudo, so a VM whose agent was talked into
something - by a repository, a web page, a tool's output - can run commands here, as you, while its window is
open. So:

- Open a window only on a VM you would let run commands on your machine, and close it when you are not
  working in it. `just deploy` opens one at its end: `just settings deploy_code --value false` stops that.
- To keep the agent at arm's length, use Remote Control - `just deploy` sets it up, and you reach it in
  claude.ai/code or the Claude apps - or plain `ssh alpha` in a terminal. Neither runs here what the VM sends,
  with two caveats for ssh: the alias says `ForwardAgent no` (`claude_ssh_forward_agent` turns it on), which
  outranks a `Host *` of your own only where the `Include` comes before it, as the one the project adds at the
  top does; and the terminal shows what the VM prints as it is, escape sequences and all, some of which a
  terminal acts on (setting the clipboard, for one).
- These narrow what a hostile VM reaches without closing the channel: a VS Code profile with no other
  extensions for these windows (VS Code's own commands stay in reach), and `"remote.autoForwardPorts": false`
  in your settings, so that no port the VM opens is forwarded here by itself.

`just code` does not repeat this on every run: [SECURITY.md](https://github.com/kawadeomkar/agent-warehouse/blob/main/SECURITY.md) and [What the VM trusts](what-the-vm-trusts.md)
say it once.

## Opening a VM

```bash
just deploy alpha                             # ends by opening alpha in VS Code; configuration printed
#   alpha: ssh alpha   |   just code alpha   (opens /home/dev/projects)
just code alpha                               # later: refresh the alias from Proxmox, then open the VM in VS Code
just code alpha --project discord-music-bot   # just that project, in a window of its own
just code alpha --session 0a608ad8-1b2c-4d3e-8f90-123456789abc   # go on with a Claude session from this machine
```

`just deploy` runs `just code` itself as its last step, for a run for one VM - the one named,
or the default VM of a bare `just deploy`, whatever other VMs exist (`deploy_code: false`
leaves it out). There, whatever keeps the window from opening - no alias, no `code` command, an SSH
connection refused, a Remote - SSH extension that would not install - is said with the `just code`
line instead of failing a deploy that is otherwise done; a VM the deploy found stopped, and so did not
configure or sign in, is said with
`just deploy alpha --start`. A deploy for several VMs
opens no window and ends with each VM's `just code` line.

`just code` looks the VM up on Proxmox, rewrites its alias in case the lease moved, checks over SSH
that the folder it is about to open is there, installs the Remote - SSH extension if VS Code does
not have it, and opens the folder with `code --remote ssh-remote+alpha`, by way of a workspace file
that also starts `claude` in a terminal of the window ([A Claude session in the
window](#a-claude-session-in-the-window)). At a terminal it is drawn live, a row for each of those steps
and the VM's ID and address at the top ([During a run](runs.md)); what it says of the window prints whole
above. A VM that is stopped - shut down, or not yet started again after its Proxmox host restarted - is
refused with the line that starts it, `just deploy alpha --only provision --start`, which does nothing more
to a VM already configured; then run the same `just code` again.

The first connection installs VS Code Server on the VM and takes about a minute; the VM downloads it
from Microsoft over HTTPS, so it needs that route out. You can also pick `alpha` in the extension's
host picker (*Remote-SSH: Connect to Host…*) and open any folder.

## Opening one project

`--project` opens one folder of `~/projects` instead: the repository's name, as `github_projects`
cloned it. A window rooted at the project is what most editor features expect - the source control
view, search, a debugger's launch configuration and the Claude Code extension all start from the
repository, where with `~/projects` open they see every repository at once. Names are checked
before anything runs: a GitHub repository name is letters, digits, `.`, `-` and `_`, so a `/`, a
`..` or a quote is refused. A name that is not there is reported with what is, and a name that
differs only in case gets a suggestion, because folder names on the VM are case-sensitive:

```
There is no project named parkbnb in /home/dev/projects on alpha. Did you mean ParkBnb? Folder names are case-sensitive.
```

The same check catches a VM that was never configured, which has no `~/projects` yet; the message
says to run `just deploy --only configure` first.

## Where the alias lives

In `~/.ssh/agent-warehouse.conf`, one block per VM, included from the
top of `~/.ssh/config` with a single `Include` line. That file and that line are the only things this
project writes in `~/.ssh` besides the VMs' host keys, kept in a file of the project's own beside it,
`~/.ssh/agent-warehouse.known_hosts`, each under the VM's identity (the block's `HostKeyAlias`) rather than its
address; the body of `~/.ssh/config` is never touched, its
permissions are left as they are, and the `Include` stays once added (an `Include` of a missing file
is harmless). An `Include` of the same file that you already have, as `~/.ssh/agent-warehouse.conf`,
`${HOME}/...` or the bare name, counts if your ssh reads it - the question is put to `ssh -G` rather
than guessed from the spelling, since `${HOME}/...` is expanded only from OpenSSH 9.9 on (Ubuntu
24.04 ships 9.6) and an `Include` below a `Host` block is scoped to that block - and your line is
left exactly as you wrote it either way. The alias is the VM's
name. If your own `~/.ssh/config` already has a `Host` entry of that name, the run refuses to write
the alias and names the line: rename the VM, or the entry, since the managed file is included first
and would otherwise override yours silently. Set `claude_ssh_config: false` to keep the project out
of `~/.ssh` altogether, or `claude_ssh_config_include: false` to write the file but add the `Include`
yourself.

Since the managed file comes first, no alias is written that ssh would take for another host: a name it reads
as an address (`192.0.2.10`, `42`, `0xbeef`), or one the inventory reaches a host by (`pve`, or the Proxmox
host's address), whose root SSH would go to the VM instead. A name with a dot gets its alias only from a run
that names it - `just deploy alpha.lab`, `just code alpha.lab` - and never from a fleet-wide one, which says
it left the VM out and leaves an alias it has as it was: on a shared host a tagged VM named `github.com` could
be anyone's, and would get this machine's `git push`.

```
# ~/.ssh/agent-warehouse.conf
# BEGIN agent-warehouse: alpha
# origin: pve
Host alpha
    HostName 192.0.2.51
    User dev
    ServerAliveInterval 30
# END agent-warehouse: alpha
```

## Keeping it current

`just deploy` and `just deploy --only configure` write the alias of each VM they finish,
`just destroy` removes it with the VM (a VM whose deletion
fails keeps its alias, since it is still there; its host key stays, under an identity no VM made later has), and `just deploy --only ssh-config`
brings the whole fleet back in step at any time: a lease that moved, a VM deleted in the Proxmox UI,
a second machine. Fleet-wide it also prunes the aliases of VMs that no longer exist. Pruning only
ever removes blocks from the managed file, never VMs, and a block is only ever written for a VM
tagged `agent-warehouse`, so a VM you run outside this project cannot be affected. Two cases are
left alone and reported instead: a name that an *untagged* VM still carries (not this project's to
reason about, exactly as `just destroy` refuses it), and anything at all while a tagged VM is listed
without a name, which is what a node that is down looks like. `just deploy alpha --only ssh-config`
refreshes one VM and never prunes.

Each block records the Proxmox host its VM was found on (`# origin: pve`), and a run against one host
prunes, re-points and removes only that host's blocks: a block another host recorded is left, so a
prune against one host never deletes another's aliases. A block with no record - every one before 2.5
wrote - is taken as this host's while the checkout has one host, and the next write of that VM
records it.

On a network whose DNS resolves VM names (a router that registers DHCP leases, or avahi with
`local`), set `claude_ssh_host_domain: lan` and every alias points at `alpha.lan` instead of the
address; a lease that moves then never matters.

**Agent forwarding is off**, and should stay off: the VM user has passwordless sudo and runs Claude
Code, and `ForwardAgent yes` would hand every key in your agent to both for the life of the
connection. Pushes go through `gh`, signed in on the VM itself - by `just deploy --only configure` from a vault
token, or by `just deploy` and `just login --only github` through GitHub's device flow (see
[Private repositories, and pushing from the VM](github.md#private-repositories-and-pushing-from-the-vm)).
`claude_ssh_forward_agent: true` turns it on if you have decided otherwise.

## What the VM gets

The `vscode_server` role writes `/etc/ssh/sshd_config.d/20-vscode-server.conf`,
which keeps `AllowTcpForwarding` and `AllowStreamLocalForwarding` on (the server is reached through
a port forward over the SSH connection) and adds a keepalive so an editor left open on a laptop that
went to sleep is reaped in minutes. Then it asks sshd what it will apply to your connection - as
the VM user, from your machine's address, so `Match` blocks count - and fails, naming the file, if
forwarding ends up off, limited to the remote direction, or removed by `DisableForwarding`: sshd
keeps the first value it reads, so a `05-*.conf` of yours wins over ours without a word, and a
`Match User dev` block applies whatever sorts first. It also raises
`fs.inotify.max_user_watches` to the 524288 VS Code recommends, or the editor warns that it cannot
watch a large workspace. None of it is VS Code's alone - Claude's sign-in in the browser comes back to the
VM over the same forwarding - so the VM gets it whichever editor you choose. `vscode_remote: false` removes
both files. The Claude Code extension
(`anthropic.claude-code`) installs on the VM's side of the connection like any other extension and
uses the VM's `claude` and its login; `remote.SSH.defaultExtensions` in your VS Code settings
installs it on every VM you open.

## When things go wrong

A connection that hangs or a server that will not start is reset with
*Remote-SSH: Kill VS Code Server on Host* (or `rm -rf ~/.vscode-server` on the VM); a `HOST KEY
VERIFICATION FAILED` means the machine at the VM's address does not hold the VM's key - another machine
answers there, or the VM was renamed in Proxmox, which gives it new host keys at its next boot - and for a
rename, `ssh-keygen -R agent-warehouse-<uuid> -f ~/.ssh/agent-warehouse.known_hosts` (the alias's
`HostKeyAlias`, which the VM's session notes print whole) clears it. If Ansible runs somewhere other than the machine
VS Code is on, copy the managed file, the known_hosts file beside it and the key there; a VS Code on Windows reads
`%USERPROFILE%\.ssh\config`, not WSL's, so point `remote.SSH.configFile` at the managed file
(`\\wsl.localhost\<distro>\home\<you>\.ssh\agent-warehouse.conf`) and keep the key readable from
Windows.

## A Claude session in the window

`just code` does not stop at the window. VS Code is handed a small workspace file that names the
folder and holds its tasks, so once the window has connected a terminal named **Claude** opens in it
running `claude` in the folder, split beside a plain **Shell** in the same folder on its left: `just
deploy` ends in a Claude session in the repository, and so does `just code` later. `just deploy` has signed the VM in already; on a VM that has not signed in to Claude
yet, the terminal runs `claude auth login` first. The sign-in page opens in the browser on your
machine - VS Code Server sends every URL a terminal opens there - and if the page shows a code, paste
it into the terminal. Remote Control then starts by itself where the `claude_code` role left a unit
waiting for the login; on a VM configured before that unit existed, or whose settings block Remote
Control, the terminal names `just deploy alpha --tags claude_code` instead. A sign-in that
fails says how to try again: *Tasks: Run Task*, *Claude*, or a window reload. Then GitHub the same
way, on a VM where `gh` is not signed in yet: the terminal shows a one-time code and opens the page to
enter it on, and `gh` becomes git's credential helper, so a push from that terminal works at once;
Ctrl-C skips GitHub, and Claude starts. The
private repositories follow on the next `just deploy --only configure` (see
[Private repositories, and pushing from the VM](github.md#private-repositories-and-pushing-from-the-vm));
`github_auth: false`, or `github_auth_method: token`, leaves GitHub out of the terminal too.

### The first time

The first time, VS Code waits for two answers that are yours to give, and Claude for one:

1. **Trust.** VS Code opens a folder it has not seen before in Restricted Mode, which starts nothing
   on its own, terminals and tasks included. Choose *Manage* on the banner, then *Trust*, and the
   terminal starts. Trusting the whole projects directory - the window `just code alpha`
   opens, without `--project` - trusts every project on alpha and every workspace file `just code`
   writes there, at once. VS Code keeps trust by alias (`ssh-remote+alpha`), so a lease that moves
   does not undo it, but a VM you rename is trusted again.
2. **Automatic tasks.** VS Code then asks whether to allow automatic tasks in trusted workspaces,
   once per workspace, in a notification at the bottom right that goes quiet after a moment (the
   bell keeps it): choose *Allow*, which writes `"task.allowAutomaticTasks": "on"` to your VS Code
   user settings, so it is asked once. If you missed the question, *Tasks: Manage Automatic Tasks*
   in the Command Palette offers the same choice.
3. **Claude's own check.** In the terminal, `claude` asks once per repository whether to trust the
   folder it was started in, as it does anywhere; the whole projects directory is pre-trusted on a
   VM with Remote Control, one project's clone is not.

After that, `just code` opens straight into Claude. It reads your VS Code user settings, and never
writes them, to say which of the two VS Code answers to expect; with `task.allowAutomaticTasks` set
to `"off"` it says the terminal will not start, and *Tasks: Run Task*, *Claude* starts it by hand in
a trusted window. Where it looks for the settings, and what it says when it cannot read them, is in
[§4.9](../architecture/lifecycle.md#49-playbookscodeyml), step 9; `vscode_user_settings_file`
([Switches](#switches)) points it at another file. Neither answer is given for you, on purpose:
Restricted Mode and the automatic-tasks question are VS Code's guard against code that runs because
a folder was opened, and this project does not turn trust off, allow terminals in untrusted windows,
or write the setting.

### What it writes

The workspace files live on the VM beside the repositories, never in one, so
`git status` stays clean: `~/projects/.agent-warehouse/<directory's name>.code-workspace` for the
whole directory (`projects.code-workspace` with the default `vm_projects_dir`) and
`~/projects/.agent-warehouse/project/<repo>.code-workspace` for one project, hidden from the
explorer. They, the session notes beside them (below) and the copies of this machine's Claude sessions
([This machine's Claude sessions on the VM](#this-machines-claude-sessions-on-the-vm)) are all `just
code` writes on the VM, and only when they would change. A
repository named `.agent-warehouse` would land on the same path; `just code` refuses to open that
name, and `github_projects` would clone it there, so leave it out of `github_projects`.

### After the first sign-in

[Remote Control](remote-control.md)
waits for exactly that login. The terminal says it starts by itself, and `just deploy alpha
--tags claude_code` keeps it on across reboots; where nothing waits for the login, it says that same
command starts it. A VM configured with an API key is already signed in, and goes straight to `claude`.

### Every window picks the folder's conversation up

The terminal runs `claude --continue`: the
folder's most recent conversation, or a new one when there is none. A window reload (*Developer:
Reload Window*, or the *Reload Required* an extension install can ask for) ends the task's process
and runs the task again, so nothing is lost but the reply in flight; `/clear` starts a fresh
conversation. Opening a window that is already open focuses it rather than starting a second
session; two windows on one project would share the conversation. Quitting `claude` ends the task;
*Tasks: Run Task*, *Claude* starts another. After `just code ... --session <id>` the terminal runs
`claude --resume <id>` instead, until a `just code` for the same project without `--session` (below).

### Leaving Claude working while you are away

A window that loses its connection - the laptop sleeps, the network drops - reconnects by itself, and
for 3 hours the VM keeps its side of the window: the VS Code server, the Claude terminal and whatever
Claude was doing, so the window picks up where it left off. After 3 hours without a window the server
shuts down, and the terminal and its `claude` end with it: the conversation stays on disk and the next
window's `claude --continue` brings it back, but whatever Claude was in the middle of stops there. The
3 hours is VS Code's own `remote.SSH.reconnectionGraceTime`, in seconds, a setting of yours that this
project never writes.

To leave Claude working for longer than that:

- **`/bg`** in the Claude terminal moves the conversation into one of Claude Code's
    [background sessions](https://code.claude.com/docs/en/agent-view), a process of its own on the VM
    that needs no window or terminal. Back in a window, `claude agents` in the shell beside the Claude
    terminal lists it, and `claude attach <name>` opens it. Background sessions are a research preview
    of Claude Code's; one that has finished and waited an hour unattended is stopped, and resumes where
    it left off when you attach.
- **[Remote Control](remote-control.md)** runs on the VM as a service of its own, so a session you start
    from claude.ai/code or the Claude app keeps working with no window at all, and you can follow it from
    your phone.
- For a long command of your own - a build, a fuzzer - `tmux` is on every VM: `tmux new -s <name>` in
    the shell, and `tmux attach -t <name>` from any later window or `ssh alpha`.

### Switches

`editor_claude_terminal: false` opens the bare folder, as before, and writes nothing
on the VM; for one run, `ANSIBLE_ARGS='-e editor_claude_terminal=false' just code alpha`.
`editor_session_notes: false` keeps the terminal and leaves the session notes out.
`claude_sessions_sync: false` keeps this machine's Claude sessions here (below).
`editor_files_dir` moves the workspace files to an absolute path of its own on the VM (outside
the projects directory, each window is trusted on its own; a directory that exists keeps its
mode). Switching off, or moving them, leaves the files written before where they were: harmless,
and `rm -r ~/projects/.agent-warehouse` on the VM removes them. `vscode_user_settings_file`
points the check at another settings file: VS Code Insiders'
`~/Library/Application Support/Code - Insiders/User/settings.json`, VSCodium's
`~/.config/VSCodium/User/settings.json`, or, for a VS Code on Windows driven from WSL,
`/mnt/c/Users/<you>/AppData/Roaming/Code/User/settings.json`.

### The Claude Code extension

The first `claude` in the terminal installs `anthropic.claude-code`
on the VM's side of the connection by itself, as it does in any VS Code terminal, and the install
may ask for a reload (which the task survives, as above). It uses the VM's `claude` and the same
login; its panel is there if you would rather have it than the terminal. To keep it out, set
`autoInstallIdeExtension` to `false` in `claude_code_settings`. Written against VS Code 1.140,
unchanged in 1.141: both gates changed in 2026 (automatic tasks off by default since 1.109,
Restricted Mode with only a banner since 1.126), so a VS Code that changes them again may ask
differently.

## This machine's Claude sessions on the VM

Every `just code` also copies the Claude Code sessions of the last 30 days on this machine - the one
you run `just` on - to the VM, the ones of the repositories the VM has checked out, so a conversation
started here can go on there. In `claude` on the VM, `/resume` lists the folder's own conversations;
press **Ctrl+A** for every project's, and the copies are among them, with their branch and when they
were last written here - all but those Claude Code leaves out of the picker everywhere: sessions started
with `claude -p` or the Agent SDK, and those whose first prompt was `/loop`, which `claude --resume
<id>` resumes all the same. Claude Desktop's sessions, which Claude Code keeps here at any age, come
over within the 30 days like any other. Picking one in its project's folder continues it there; picked anywhere else,
Claude Code says it is from a different directory and copies the `cd` to it. The window's own terminal
never picks a copy up: `--continue` looks only at the folder's own conversations, and a copy is filed
under the folder name it has here (`-Users-you-homeserver`), not the checkout's.

One copy sends at most `claude_sessions_sync_max_size` (256M by default; `1G`, or `0` for no bound) that the VM
does not have yet: the newest sessions first, each with its tool results and subagents while it fits, and the rest
stay here, their number said. A session copied before counts only what it grew by since, so a long one goes on
being copied once it is there; its first copy is the whole of it, which a larger bound, or `--session`, sends once.
Before it sends, the run says how many sessions and how much, since the copy shows nothing while it runs, and over a
slow link to the VM even the bound takes minutes.

A session belongs to the repository its first directory is a checkout of, by its `origin` remote,
compared as owner and repository with the origin of each checkout in `~/projects` on the VM: a renamed
clone or a session started in a subfolder still matches, another owner's repository of the same name
does not. On the VM only a GitHub origin counts, as `just deploy` clones one, so a checkout there with a
local path or another host's URL for its origin is given none of this machine's sessions; here any form
does, an SSH alias of yours for GitHub included. So a VM that names a GitHub repository gets the sessions of
a checkout here of that owner and name on any host, a GitLab or company server's too. A session of a Claude worktree (`.claude/worktrees/<name>`) whose folder is gone
goes by its checkout, and one of a clone that is gone by the `githubRepoPaths` Claude Code keeps in
`~/.claude.json`. Left here: sessions without a message, and sessions written by a Claude Code newer
than the VM's - which installs the stable channel - named in the run's message with the line that installs the
newest of their versions there, `just ANSIBLE_ARGS='-e claude_code_version=<version>' deploy <vm> --tags
claude_code`; a version installed so stays at later configures, which install a channel's version only where Claude
Code is missing.

What a copy is. The transcript, its complete lines only - a session open here while it is copied
comes over as far as it was written - and its `tool-results/` folder, in the VM's `~/.claude/projects/`
under the folder name it has here, 0600 in 0700 folders. It keeps its file time here while that is
within 14 days; an older one's copy gets the time it was copied, renewed every 14 days, since Claude Code
deletes a transcript 30 days after its file time and would otherwise take a session you asked for by ID
before you resumed it. Its tool results take the same time, since Claude Code deletes those by their own
age too. Each copy gets four edits and nothing else, so that it resumes as the VM's own: the
memory folder its recorded system prompt names, and the folder of the tool results it saved, become the VM's;
a session bound to a Claude worktree goes on in the main checkout; and a last entry names the checkout. What
the assistant said, signed thinking included, stays as it was. [§4.9](../architecture/lifecycle.md#49-playbookscodeyml)
says why each.

With each session go its subagents' transcripts (`claude_sessions_sync_subagents`, on by default), so a
subagent it started can be looked into on the VM, and continued there once the session is resumed: Claude
continues one by its ID. They count toward the bound, and a long session's subagents can hold more than its
own transcript; `false` keeps them here. A subagent's transcript gets the memory folder's edit its session's
does, and one continued on the VM is never overwritten. A subagent that worked in a folder only this machine
has, such as a worktree in a session's scratchpad, finds no such folder there.

Never copied: credentials, the list of running sessions, shell snapshots, the file history - so `/rewind` on the VM
rewinds the conversation but restores no file edited before the handoff - and `~/.claude.json`.
`just code` copies nothing back from the VM and writes nothing on this machine; what the VM has of its
own, `just destroy` saves here before it deletes the VM
([Its Claude Code sessions](destroy.md#its-claude-code-sessions)).

A copy continued on the VM is never overwritten. `just code` records the digest of each copy it writes
(`~/projects/.agent-warehouse/claude-sessions/manifest.json`) and replaces a copy only while it is
still exactly that; the run names the sessions it left as they are, and those that changed both there
and here since. So is a copy a `claude` on the VM has open, which would take that `claude`'s next lines
onto what replaced it, and a session in two folders here, which `claude --resume <id>` could resume from
neither once both are on the VM. A copy that `/cd` on the VM moved to another folder is left the same way, and never
written again beside it: Claude Code resumes an ID from another folder only when one folder has it. A
session `/cd` moved here takes its copy along, while the VM has not continued it. rsync brings them,
compressed, into a mirror in the same directory - about as much disk again - so the first run takes
seconds on a LAN and later runs send only what was added. A file rsync could not send - one that
vanished as it ran, or rsync missing on either machine - leaves those sessions out, named, and the window
opens all the same. The
first answer in a copied session reads the whole conversation again, uncached: the prompt cache is
Anthropic's, not this machine's, but a copy's recorded system prompt names the VM's memory folder,
which makes it another prompt, and a cache entry lasts only minutes after its last request anyway.

### Auto memory

Claude Code keeps notes of its own for each repository, its auto memory, in `~/.claude/projects/<folder>/memory/`,
and loads `MEMORY.md` from there at the start of each conversation. `just code` copies this machine's into the memory
folder of the VM's checkout of the same repository - matched by git origin, as the sessions are - whether or not the
sessions go: a fresh conversation on the VM starts with the notes one here would, and a copied session's recorded
prompt already names that folder. A repository whose notes are in two folders here, two clones each with notes of its
own, is left out and named.

A note is replaced only while it is what the last copy wrote, as a session's copy is: one a session on the VM added
or changed stays as it is, and the run names it. `MEMORY.md` is one of them, so once a session on the VM adds a line to
it, notes written here after that arrive unindexed until you merge the two. A note gone here is removed there while it
is still as copied. Beside them goes one note of just code's own, `carried-by-just-code.md`, indexed by a line at the
end of `MEMORY.md`: where this machine's checkout and memory folder are on the VM, since the notes and the copied
sessions name this machine's paths. `claude_memory_sync: false` keeps the notes here.

### Your instructions and settings

Your global instructions, `~/.claude/CLAUDE.md`, and the files of `~/.claude/rules/` go to the VM's
(`claude_instructions_sync`), so Claude there follows what Claude here does - unless
`claude_code_global_instructions` writes the VM's `CLAUDE.md`, which then stays the role's. What `CLAUDE.md` imports
with `@path` stays here, and the run names it.

So do the settings that mean the same on any machine (`claude_settings_sync`): the model, effort and preferences
(`model`, `modelSettings`, `effortLevel`, `theme` and the like), what commits made there say of Claude
(`attribution`), your permission rules and mode (`permissions.allow`, `ask`, `deny`, `defaultMode`), and auto mode's
rules (`autoMode.allow`, `soft_deny`). Each is set in the VM's `~/.claude/settings.json` beside what is there, by the
same rule as a note: one changed on the VM - `/model` there writes `model` - is never overwritten. Never: `env` and
every other setting that holds or makes a credential, this machine's folders (`permissions.additionalDirectories`),
`autoMode.environment`, which describes this machine to auto mode, plugins (their own step), MCP trust, and a key the
`claude_code` role writes - `claude_code_settings`' own, the update channel. `hooks`, `statusLine` and
`fileSuggestion` run a program, here one of this machine's, so they stay too unless `claude_settings_sync_keys` names
them; any setting taken out of that list goes from the VM while it is still as carried, and one `claude_code_settings`
names afterwards is the role's there; a setting just code does not know stays and is named. The run says what stayed, and why. While this machine's `settings.json` is no JSON object just
code can read, nothing of the VM's changes.

### Plugins

The plugins installed and enabled for you here, from a marketplace on GitHub, are installed on the VM by its own Claude
Code (`claude_plugins_sync`): `claude plugin marketplace add` for a marketplace it lacks, then `claude plugin install
<name>@<marketplace> --scope user`, never accepting a command a marketplace declares for you. One that needs such a
command, or whose install fails, is named with the line that installs it by hand there; a plugin from a marketplace on
this machine's disk, installed for one project, or disabled here stays. Nothing is ever uninstalled there. What a
plugin needs beside itself is the VM's to have: `rust-analyzer-lsp` runs `rust-analyzer`, which `rustup component add
rust-analyzer` puts in the toolchain a repository pins.

### Prompt history

The prompts typed in the sessions copied join the VM's prompt history (`claude_history_sync`), so the up arrow and
Ctrl+R there find them, under the VM checkout's folder: each line once, by its session and time, however often
`just code` runs. A session the copy left as it is - continued on the VM - keeps its prompts here. They ride with the sessions, so none go while `claude_sessions_sync` is off, and at most 8 MB of them
go in one copy.

### Skills, agents and commands

Your own skills (`~/.claude/skills/`), subagents (`agents/`), commands (`commands/`), output styles, workflows, themes
and `keybindings.json` go to the VM's `~/.claude` (`claude_extensions_sync`), by the same rule as a note: a file changed
there stays. Not the skills your account syncs (`skills/synced/`), which Claude Code downloads at the VM's own
sign-in. A skill's scripts run on the VM only where the skill runs them, and a script that can run here can there.
Past 2000 files or 64 MB they stay here whole, and the run says so; `rules/` past 1000 files or 16 MB does too.

### What is not read here

`just code` follows no link in `~/.claude`: a `CLAUDE.md` kept with your dotfiles, or a skill installed as a link to
where it lives, is not carried, and the run names it. Copy the file in, or link the other way round. Neither it nor a
file `just code` may not read is ever taken for one you deleted: what an earlier run carried of it stays on the VM.
Nor is anything taken from the VM while the Claude Code directory `just code` reads (`CLAUDE_CONFIG_DIR`, else
`~/.claude`) is not there at all.

### Continuing one session

`just code alpha --session <id>` opens one session in the Claude terminal, in the window of
its project, and copies it whatever its age. It needs that terminal: with `editor_claude_terminal: false`
it refuses, naming the `claude --resume <id>` to run there yourself. The ID is its transcript's name, as `/resume` and
`/status` show it. The session's code comes through git, not the transcript, so before anything is
written `just code` checks, and refuses with what to do:

- The session is in one folder here, has messages, and is not open in a `claude` here or on the VM.
- Its checkout here has no uncommitted or untracked change, and the branch it last ran on is pushed:
    where `origin`'s is. Every git command here runs as `git --no-optional-locks`, so none takes the
    index lock a git, an editor or a Claude of yours could be holding.
- The VM's checkout of the repository has a clean tree, is on a branch - or on a detached `HEAD` that a
    branch or tag holds, so moving it loses no commit - and has no `claude` working in it.

It then fetches the branch on the VM and brings the checkout to the same commit, by fast-forward
only: switched to the branch, created at that commit to track `origin`'s where the VM has none, and a
branch of the VM's own with commits not here is left as it is, and refused. So is a move that would
replace a file git does not track there - an ignored one, which `git status` does not show and git
overwrites without asking - named, for you to move out of the way. That is the one write
`just code` makes in a repository. The terminal runs `claude --resume <id>`: the same session under its
own ID, never a fork, and a resume that fails says why and starts no other `claude`. The window's task
resumes it again on every reload, even after `/clear`, until a `just code` for the same project without
`--session` - each project's window has a workspace file of its own, and a run for another project, or for
the whole directory, leaves this one's as it is. Three
things to know:

- Claude Code asks whether to trust the project's folder the first time it runs there; that answer
    is yours, as on any machine.
- If the project's window is open already, VS Code only brings it to the front: reload it
    (*Developer: Reload Window*) to resume the session.
- With `github_projects_update_existing` on, the next `just deploy --only configure` checks the default
    branch out again. Remote Control's sessions start in `~/projects`, above the checkout, where the
    check for a `claude` working in it cannot see them; the run says when Remote Control is running.

A transcript holds whatever its tools printed - command output, tokens, the contents of files - so
every VM `just code` opens gets those of its repositories' sessions. `claude_sessions_sync: false` in
`local.yml` keeps them here; `--session` still copies its one session.

## This machine's repository on the VM

Started from your checkout of a project, `just code <vm> --project <repo>` - or `just deploy <vm> --project <repo>` -
carries that repository to the VM's checkout of it: every local branch and tag, those never pushed among them, and the
checkout there switched to the branch you are on here, with your changes not committed - tracked files changed or
deleted, new files - all of them or none, unstaged there: `git diff HEAD` says the same at both ends. A new file the
rules below leave stays here, said, and so does a vault you decrypted in place, whose change would carry its secrets;
the rest go. The two must be one repository by their git origins, whatever
the folders are called; started in another repository, the run says why it carried nothing. Later runs carry what
changed, in seconds.

Nothing the VM made is overwritten. A branch moves there only while the VM added nothing to it since the last sync,
so an amend, a rebase or a reset here follows; a branch with commits of its own there is left, said, and kept for
NEXT STEPS, as is one that has commits from its upstream this machine has not fetched (pull here, and the next run
moves it), and one ahead (pulled there) is counted. A branch new to the VM tracks what it tracks here on origin. A branch a worktree there has out is never moved under it. The
checkout there is switched or moved, and given your changes, only when nobody's claude works in it, nothing git does
not track is in the way - but a file the sync itself carried there as an ignored one, which gives way to the same
file committed here - no file git ignores there is where one of your new files goes, and nothing in its `git status`
is anything but what the last sync wrote there; otherwise it stays as it is, and the run says why. A change made there, even to a file the sync wrote, or staged there, is the
VM's own. A merge or rebase under way here, or changes past `repo_sync_max_size`, keep the tree here too. Nothing is deleted there: a branch deleted here stays.
A shallow clone there (`github_projects_depth`) refuses a branch that forks behind its shallow point: that one is
said, with `git fetch --unshallow` there as the way to have it. From the agent-warehouse checkout itself,
`just code <vm> --project agent-warehouse` is started from a checkout of that project too.

```bash
alias aw='just --justfile ~/agent-warehouse/justfile'
cd ~/code/my-app && aw code app --project my-app
```

The files git ignores - plan files, working documents - go too, each file to the VM only where it has no file of its
own at that path, or still the one the last sync wrote: a file edited or made there is left, and counted. One that
git ignores here only by this machine's own exclude file is named in a block of the VM checkout's `.git/info/exclude`,
so `git status` there says what it says here. A file deleted here goes there only when the same folder syncs again
and the VM's copy is still the sync's: a worktree of the repository, which has none of the main folder's ignored
files, removes nothing, and a file only left out of a run - `repo_sync_ignored: false`, past `repo_sync_max_size`,
excluded - stays there. Should rsync fail part way - a file gone between the read and the send, a dropped
connection - the run says so with rsync's words, and a file that did not arrive as read is not written; the next run
sends it. What is never carried, from any repository and at any depth: this project's own secrets and records (a vault, its
password, an inventory, `local.yml`, `.logs/`, `.sessions/`, `.cache/`), and any file that starts as an Ansible vault
or a private key does - and a wholly ignored folder holding one is left whole. Left unless the repository's
`.worktreeinclude` names it, as Claude Code reads that file for a new worktree: a credential by its name (`.env`, `*.pem`,
`id_*` ...) and a derived tree (`node_modules/`, `.venv/` ...). A link, a repository inside the repository, and your own
`repo_sync_exclude` patterns are left too - your patterns from the changes to tracked files as well, which the names
above are not asked of, so that a tracked `.env.example` you edit still goes. `repo_sync_max_size` (256M) bounds what one sync sends; `repo_sync: false`
turns it off, `repo_sync_ignored: false` leaves the ignored files out (those already there stay). A folder all of
whose files are derived, such as `node_modules/`, is named as one, never read file by file. The tiers go by name and
by those two headers: a
secret in an ignored file under an ordinary name would be carried, so the report names everything it leaves, by rule.

## Session notes

Every time the window opens - from `just deploy`, `just code`, *Open Recent*, a restart that restores
it, or a reload - a page about the VM opens beside the Claude terminal, as a rendered preview:

- **Signed in**, read from the VM as the window opened, at a time given in your machine's time zone:
  the Claude Code account and plan, whether Remote Control is live, the GitHub account `gh` is signed in
  as, and Tailscale's state with the `ssh` command that reaches the VM over it.
- **At a glance**: VMID, node, cores, memory and disk, the user, the projects folder, the LAN
  address, and the SSH alias.
- **Reach this VM**, **Start here**, **From your machine** (each `just` command with this VM's name,
  and `--editor` on the `just code` lines after a run whose `--editor` was not the `editor` setting's, so they
  reopen the window of the editor that run used), **Written on your machine** (everything the project writes on
  your machine outside its checkout, what writes it and what takes it out:
  [On the controller](../architecture/security-model.md#on-the-controller)), and **When something is off**.

`just code` writes it as `alpha-session.md` in `~/projects/.agent-warehouse/session/` (0600, in a
0700 directory: it names the accounts signed in there), never in a repository. The window's
**Session notes** task runs beside the Claude terminal, never holding it up: it rewrites only the notes'
**Signed in** section each time, in a second or two, and opens them, which usually moves the keyboard
focus to the notes - click the Claude terminal to type. A sign-in made in the Claude terminal refreshes
the section a few seconds later; *Tasks: Run Task*, *Session notes* refreshes it by hand. Whatever the
task meets, it opens the notes it has and finishes.

The time in **Signed in** is in the zone of the machine you ran `just code` on, not the VM's clock, which
keeps UTC: `TZ` when your shell sets it (`TZ=Europe/Berlin just code ...`), otherwise the zone your system
is set to, and UTC when neither names one, or when the VM does not know the zone. `just code` and `just
deploy` write it into the window's tasks, so after you change time zone the window keeps the old one until
the next `just code`.

In the window of the whole projects directory the notes' folder shows in the Explorer as
**alpha · session**. A one-project window (`--project`) opens the same file from outside its own
folder, which VS Code asks about once for that window - *Open* is remembered - unless you trusted
the whole directory's window, which covers it. The folder is not added to the project's window on
purpose: VS Code trusts a workspace only as far as the folders you trusted, so a folder added later
would put a window you trusted back in Restricted Mode, without its Claude terminal. Like the
terminal, the notes need a trusted window and automatic tasks; until then `just code` says where
they are. `editor_session_notes: false` turns them off.

## See also

- Settings: [Editor and SSH](../reference/settings.md#editor-and-ssh)
- Role: [`vscode_server`](../reference/roles/vscode_server.md)
- How it works: the aliases in [§4.8](../architecture/lifecycle.md#48-playbooksssh_configyml-and-playbookstasksssh_configyml),
  `just code`, its workspace file and the session notes in
  [§4.9](../architecture/lifecycle.md#49-playbookscodeyml), and the VM's side in
  [§5.7](../architecture/roles.md#57-vscode_server--what-an-editors-remote-server-needs-on-the-host)
