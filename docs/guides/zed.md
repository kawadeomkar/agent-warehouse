# Zed

[Zed](https://zed.dev) opens a VM over SSH as VS Code does: the window is on your machine, the files, the
terminal and the language servers are on the VM. `just code` opens it there when Zed is the editor you
chose:

```bash
just settings editor --value zed     # every just code, and just deploy's last step
just code alpha --editor zed         # this once
```

One editor at a time: the `editor` setting picks it, VS Code by default (see [VS Code](vscode.md)).

## What `just code` does

Everything up to the window is as it is for VS Code - the VM found and reached, its alias refreshed in
`~/.ssh/agent-warehouse.conf`, this machine's recent Claude sessions copied there, the session notes
written - and then it runs:

```bash
zed ssh://alpha/home/dev/projects
```

Zed reads the alias from `~/.ssh/config`, as `ssh alpha` does, so it follows the address DHCP gave the
VM. The first connection installs Zed's remote server in `~/.zed_server` on the VM, the version of the
Zed you run, and takes a moment. `zed` must be on your `PATH`: on macOS, run **cli: install cli binary**
from Zed's command palette (**cli: install** in earlier versions); on Linux, Zed's package puts `zed`
there, or `zeditor` on some distributions, which `just code` takes too.

Nothing of VS Code's is written for Zed - no workspace file, none of its helpers. For Zed, `just code` writes
two files, in its own directory on the VM: `.claude-on-proxmox/.zed/tasks.json`, the **Claude (just code)** task
(below), and `.claude-on-proxmox/helpers/claude-start.sh`, the helper that task runs. Never a repository's
`.zed/`, and never your own Zed settings, keymap, tasks or trust. The tasks file starts with the line
`// Written by just code (claude-on-proxmox); edits here are overwritten.`, which Zed reads as a comment: a
`tasks.json` there without it is not `just code`'s, and is left as it is, which `just code` says. And `just code`
refuses to write at all when its directory (`editor_files_dir`) is inside a checkout - a folder below the
projects directory with a `.git` - for Zed and VS Code alike: it writes beside the repositories, never in one.

`just code` runs the first `zeditor` or `zed` on your `PATH` that says it is Zed when asked its version: ZFS's
event daemon is called `zed` too (in `/usr/sbin` on Debian and Ubuntu, `/usr/bin` on Arch), and is never handed the
VM.

## The settings, in Zed

| Setting | In Zed |
|---|---|
| `editor_claude_terminal` | The **Claude (just code)** task and its helper, which you start, since Zed starts nothing by itself (below); false writes neither. `--session` does not need it. |
| `editor_session_notes` | The notes are written, without the refresh VS Code's window gives them; you open them once, and Zed reopens them with the window (below). |
| `editor_files_dir` | Where just code keeps its own files on the VM - the notes, the copied sessions - as for VS Code. The task and its helper are written only where it is inside the projects directory, the folder of the window that lists the task. |
| `claude_sessions_sync` | As for VS Code: this machine's recent sessions copied to the VM, for `/resume` there. |

Zed shows just code's own directory - `.claude-on-proxmox/` in the projects directory, the copied sessions' mirror
in it - in its project panel and search, which VS Code's window hides. Its transcripts hold whatever their tools
printed. To keep them out of Zed's file finder, search and its own AI features, add
`"file_scan_exclusions": ["**/.claude-on-proxmox/claude-sessions"]` (beside Zed's own defaults) to your Zed
settings; this project writes none of them for you. Exclude `claude-sessions/` alone: excluding the whole of
`.claude-on-proxmox` would hide the Claude task and its helper from Zed too.

## The Claude task

Zed runs no task when a window opens - its one task trigger is a Git worktree's creation - so no Claude
terminal starts by itself as it does in VS Code. It is one step away instead: in the window of `just code
alpha`, the whole projects directory, run **task: spawn** (`cmd-shift-r` on macOS, outside the terminal) and
pick **Claude (just code)**. Each spawn opens a terminal tab of its own, so one never ends a Claude already
running. In it, the helper runs `claude --continue`, picking the folder's last conversation up, or a fresh
`claude` where there is none to continue - and a fresh `claude` too where a `claude` of a terminal already has
the folder open, so two never share one conversation. The tab's Rerun button restarts that tab's Claude: that is
Zed's, whatever a task says (all seen on Zed 1.26.2).

Zed lists the task from `.claude-on-proxmox/.zed/tasks.json`, inside the projects directory's window, so a
one-project window (`--project`) does not list it: there, open a terminal - the terminal panel - and run
`claude`, or use a task of your own (below). In the projects directory's window, **task: spawn** lists your
repositories' own `.zed/` tasks beside it, but not their `.vscode/tasks.json`: Zed leaves those out of a window
that has a `.zed` tasks file, and just code's is one. A `--project` window lists them as usual.

Either way, that terminal is the VM's, and so is that `claude`: signed in by `just deploy` (or `just login
alpha --only claude`), with the VM's `~/.claude`, and Remote Control beside it. `just code alpha --session <id>
--editor zed` brings the checkout to the session's commit and copies the session, as for VS Code, and says the
`claude --resume <id>` to run in that checkout's terminal.

Zed's terminal has no browser to hand a page to (no `$BROWSER`), so a `/login` started there prints the page
for you to open and asks for the code it shows; `just login alpha --only claude` opens it for you.

Closing the window ends that `claude`, and the Agent Panel's (below): Zed's server on the VM stops with its
window, and everything it started with it. To leave Claude working with the window closed, use
[Remote Control](remote-control.md), which runs on the VM on its own.

### One key, your own task

The project binds no key and tags its task with nothing: a tag is what a key runs, and one this project
published, any repository you clone could put on a task of its own. For one key, keep a task of your own in your
own `~/.config/zed/tasks.json`, merged into its list:

```json
{
  "label": "Claude on the VM",
  "command": "[ -f \"$HOME/projects/.claude-on-proxmox/helpers/claude-start.sh\" ] && bash \"$HOME/projects/.claude-on-proxmox/helpers/claude-start.sh\"",
  "tags": ["your-own-tag"],
  "allow_concurrent_runs": true,
  "use_new_terminal": true
}
```

and a key for it in your `keymap.json`, as one object of its list:

```json
{
  "context": "Workspace",
  "bindings": {
    "ctrl-alt-m": ["task::Spawn", { "task_tag": "your-own-tag" }]
  }
}
```

Put a name of your own in place of `your-own-tag`, the same in both, and keep it to yourself: the key runs every
task that carries the tag, in every window, a repository's too. The task runs `just code`'s helper on the VM; in a
window of a folder on this machine it finds no helper and does nothing. `ctrl-alt-m` is free in Zed's default
macOS and Linux keymaps and in vim mode (`ctrl-alt-c` is not, on Linux); Zed's keymap editor shows a conflict with
a key of your own. With another `vm_projects_dir` or `editor_files_dir`, change the path to match: the helper is
`<editor_files_dir>/helpers/claude-start.sh`, there only where that directory is inside the projects directory.

### Claude with no key (Terminal Threads)

Zed's Agent Panel can start a terminal of its own, a Terminal Thread, and run a command in it: add the same
guarded command to your Zed settings, merged into an `"agent"` object you may have already:

```json
"agent": {
  "terminal_init_command": "[ -f \"$HOME/projects/.claude-on-proxmox/helpers/claude-start.sh\" ] && bash \"$HOME/projects/.claude-on-proxmox/helpers/claude-start.sh\""
}
```

Then **agent: new terminal thread** runs it, on the VM. The thread the Agent Panel shows when the window closes
comes back with the window and runs it again - hidden, when the panel was closed (seen on Zed 1.26.2). The command
is typed into the shell, so it lands in the VM's shell history, and it reaches every Terminal Thread of every
project: in a local one it does nothing. The helper is what makes the thread and the task safe together: neither
starts a second `claude` on a conversation one already has open.

### The shell

Once the terminal panel has been used in a window, Zed starts a shell in the folder with it as the window opens.
`terminal.starts_open` does not help in a window over SSH: the panel stays hidden, and its shell starts in `~`
(seen on Zed 1.26.2).

## Claude, in Zed's Agent Panel

Zed can also run Claude as an agent of its own: **Claude Agent**, which you install from the Agent Panel's
registry. For a folder on the VM, Zed's remote server installs it on the VM (in `~/.local/share/zed/external_agents/`)
and runs it there, as the VM's user in the folder Zed opened: its tools run on the VM, its transcripts go into the
VM's `~/.claude`, and it uses the VM's claude.ai login, since Zed starts it with `ANTHROPIC_API_KEY` empty. That
was seen on Zed 1.26.2 with Claude Agent 0.100.0, not read from Zed's documentation, which does not say. Three
things set it apart from the terminal's `claude`:

- It is a Claude Code of its own: the one its npm package bundles, at the version Zed's registry installs, not
  the one `just deploy` installs and keeps up to date (`claude_code_version`).
- If it asks you to sign in, use your claude.ai account, never an API key: an API key on the VM turns Remote
  Control off ([An API key instead of a claude.ai login](remote-control.md#an-api-key-instead-of-a-claudeai-login)).
- It ends with the window, as the terminal's `claude` does.

The terminal stays the way this project sets up: its `claude` is the one `just deploy` installed, with Remote
Control beside it.

## In `just status`

`just status` counts a VM's Zed windows from the processes of Zed's remote server there, one daemon per project
it serves, and a reboot waiting names them beside the claude processes, as "Zed windows". A window's daemon ends
within seconds of the window closing (seen on Zed 1.26.2), so the count follows the windows; what a connection
that dropped leaves behind - this machine asleep - was not tried. The count is under Zed's key in
`editor_windows_by`, which is not among the keys `just status --json` promises: `editor_windows`, every editor's
windows together, is. Under ACCESS, the `just code` line is named for Zed when `editor` is `zed`.

## What Zed decides

Zed opens a folder it has not seen before in Restricted Mode: no project settings, language servers or MCP
servers until you trust it. Trust is per folder, tracked per host. Zed asks nothing: the Restricted Mode badge
shows at the top left of the window once the window meets something it holds back, a language server, and
trusting the folder is a click there (seen on Zed 1.26.2). Trusting the projects directory's window trusts every
checkout in it - their project settings, language servers and MCP servers - so do not trust it for the Claude
task alone: on Zed 1.26.2 the task is listed in Restricted Mode too.

A newer Zed holds a folder's tasks back until the folder is trusted (Zed's change #66284, on its main branch, seen
on Nightly 1.28.0). If **task: spawn** lists no **Claude (just code)** and the window shows Restricted Mode, use
your own task ([above](#one-key-your-own-task)), a Terminal Thread, or type `claude` in the terminal. Trusting is
yours to decide, and this project never decides it for you: it never sets `trust_all_worktrees`.

## What the VM gets

The same as for VS Code ([VS Code](vscode.md#what-the-vm-gets)): the `vscode_server` role's sshd drop-in,
which keeps the port forwarding on - Zed's own port forwards use it, and so does Claude's sign-in in
the browser - and the higher inotify limit Zed's file watcher wants. It keeps VS Code's name through 1.x.

## Session notes

The notes are written as for VS Code, at `<projects>/.claude-on-proxmox/session/<vm>-session.md`, and
`just code` says where, worded for Zed. After `just code alpha --editor zed` with the `editor` setting at VS Code,
their `just code` lines say `just code alpha --editor zed`, which opens this window again; the bare line opens
VS Code's. Zed does not open them with the window the first time: its command takes
one `ssh://` path (Zed 1.26.2 refuses a second), and a second command would race the window the first is still
connecting. Open them once, in the window of the whole projects directory, from the project panel under
`.claude-on-proxmox/session/`: Zed reopens the tab with that window from then on, as it does any tab left open
(seen on Zed 1.26.2). Nor can Zed refresh their **Signed in** section from the VM as the window opens, so that
section says to run `just status <vm>` on your machine instead.

## See also

- [VS Code](vscode.md), the default editor, and what both share
- [Remote Control](remote-control.md), the same Claude session from claude.ai and the Claude app
- Settings: [Editor and SSH](../reference/settings.md#editor-and-ssh)
