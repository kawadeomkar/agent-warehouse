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
`~/.ssh/claude-on-proxmox.conf`, this machine's recent Claude sessions copied there, the session notes
written - and then it runs:

```bash
zed ssh://alpha/home/dev/projects
```

Zed reads the alias from `~/.ssh/config`, as `ssh alpha` does, so it follows the address DHCP gave the
VM. The first connection installs Zed's remote server in `~/.zed_server` on the VM, the version of the
Zed you run, and takes a moment. `zed` must be on your `PATH`: on macOS, run **cli: install cli binary**
from Zed's command palette (**cli: install** in earlier versions); on Linux, Zed's package puts `zed`
there, or `zeditor` on some distributions, which `just code` takes too.

Nothing of VS Code's is written for Zed - no workspace file, none of its helpers - and nothing of Zed's
either: not a repository's `.zed/tasks.json`, not your own Zed settings.

`just code` runs the first `zeditor` or `zed` on your `PATH` that says it is Zed when asked its version: ZFS's
event daemon is called `zed` too (in `/usr/sbin` on Debian and Ubuntu, `/usr/bin` on Arch), and is never handed the
VM.

## The settings, in Zed

| Setting | In Zed |
|---|---|
| `editor_claude_terminal` | Nothing: Zed starts no terminal by itself, so you start `claude` (below). `--session` does not need it. |
| `editor_session_notes` | The notes are written, without the refresh VS Code's window gives them (below). |
| `editor_files_dir` | Where just code keeps its own files on the VM - the notes, the copied sessions - as for VS Code. |
| `claude_sessions_sync` | As for VS Code: this machine's recent sessions copied to the VM, for `/resume` there. |

Zed shows just code's own directory - `.claude-on-proxmox/` in the projects directory, the copied sessions' mirror
in it - in its project panel and search, which VS Code's window hides. Its transcripts hold whatever their tools
printed. To keep them out of Zed's file finder, search and its own AI features, add
`"file_scan_exclusions": ["**/.claude-on-proxmox"]` (beside Zed's own defaults) to your Zed settings; this project
writes none of them for you.

## Claude, in Zed's terminal

Zed runs no task when a window opens, so no Claude terminal starts by itself as it does in VS Code. Open
a terminal in the window - the terminal panel - and run `claude` there. That terminal is the VM's, and
so is that `claude`: signed in by `just deploy` (or `just login alpha --only claude`), with the VM's
`~/.claude`, and Remote Control beside it. `just code alpha --session <id> --editor zed` brings the
checkout to the session's commit and copies the session, as for VS Code, and says the
`claude --resume <id>` to run in that terminal.

Zed's terminal has no browser to hand a page to (no `$BROWSER`), so a `/login` started there prints the page
for you to open and asks for the code it shows; `just login alpha --only claude` opens it for you.

Closing the window ends that `claude`, and the Agent Panel's (below): Zed's server on the VM stops with its
window, and everything it started with it. To leave Claude working with the window closed, use
[Remote Control](remote-control.md), which runs on the VM on its own.

## Claude, in Zed's Agent Panel

Zed can also run Claude as an agent of its own: **Claude Agent**, which you install from the Agent Panel's
registry. For a folder on the VM, Zed's remote server installs it on the VM (in `~/.local/share/zed/external_agents/`)
and runs it there, as the VM's user in the folder Zed opened: its tools run on the VM, its transcripts go into the
VM's `~/.claude`, and it uses the VM's claude.ai login, since Zed starts it with `ANTHROPIC_API_KEY` empty. That
was seen on Zed 1.26 with Claude Agent 0.100.0, not read from Zed's documentation, which does not say. Three
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
it serves. A window's daemon ends within seconds of the window closing (seen on Zed 1.26), so the count follows
the windows; what a connection that dropped leaves behind - this machine asleep - was not tried. It is not among the
keys `just status --json` promises: `editor_windows`, every editor's windows together, is.

## What Zed decides

Zed opens a folder it has not seen before in Restricted Mode: no project settings, language servers or
MCP servers until you trust it, once, per host. Zed asks nothing: it says Restricted Mode at the top left of
the window, and trusting the folder is a click there. That is yours to decide, and this project never decides
it for you: it never sets `trust_all_worktrees`.

## What the VM gets

The same as for VS Code ([VS Code](vscode.md#what-the-vm-gets)): the `vscode_server` role's sshd drop-in,
which keeps the port forwarding on - Zed's own port forwards use it, and so does Claude's sign-in in
the browser - and the higher inotify limit Zed's file watcher wants. It keeps VS Code's name through 1.x.

## Session notes

The notes are written as for VS Code, at `<projects>/.claude-on-proxmox/session/<vm>-session.md`, and
`just code` says where, worded for Zed. Zed has no task to open them with the window, nor to refresh their
**Signed in** section from the VM as it opens, so that section says to run `just status <vm>` on your machine
instead: in the window of the whole projects directory, open them from the project panel, under
`.claude-on-proxmox/session/`.

## See also

- [VS Code](vscode.md), the default editor, and what both share
- [Remote Control](remote-control.md), the same Claude session from claude.ai and the Claude app
- Settings: [Editor and SSH](../reference/settings.md#editor-and-ssh)
