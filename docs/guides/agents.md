# Agents in other projects

Claude Code sessions in any of your projects can use this checkout's commands through an MCP server, so that you can
ask, from wherever you work, "what are my agent-warehouse VMs doing?". Each of the server's tools is the `just`
command of the same name, with the same options, run in this checkout: every rule and refusal is the command's own.

| Tool | The command | Does |
|---|---|---|
| `status` | `just status --json` | every VM, its checkouts and what needs doing next, read without changing anything |
| `settings` | `just settings --json` | a role's or a setting's value and where it was set; secrets said set or not, never shown |
| `logs` | `just logs` | the run logs, newest first |
| `log` | - | one run log, by its name, made plain text: its last lines, or any part of it from the line asked for |
| `deploy` | `just deploy` | creates, configures and signs in VMs, and opens one in your editor |
| `login` | `just login` | signs VMs in to Claude, GitHub and Tailscale |
| `code` | `just code` | opens a VM in your editor, with this machine's sessions copied there |
| `install` | `just install` | syncs the environment every run and the server use; Claude Code asks you first, every time |
| `jobs`, `job` | - | the runs started here; one run followed to its next wait for you, or its end |
| `cancel` | - | stops a run, as Ctrl-C at a terminal |

`destroy`, `init` and changing a setting stay at the terminal for now.

## Runs

A run that changes something is a job: it runs on its own, whatever happens to the call that started it, the server,
or the session. Its lines show under the running call as it goes, and after two minutes Claude Code moves the call
into the background, where the result still arrives at the end. A deploy's sign-ins start minutes in, when nothing on
your screen says a page waits for you, so the call returns early the first time the run waits in your browser: the
agent tells you what the page is for, then follows the run with `job` to its next wait or its end.

- **Someone at the screen.** In an interactive session (`claude` in a terminal) the run's pages open here and the
  sign-ins run, with nothing asked of you at a terminal; in `claude -p`, over SSH, or with `--attended 0` the run is
  taken for one nobody watches, as a run with no terminal always was. A sign-in that falls back to pasting a code
  needs a terminal: the agent hands you its `just login <vm> --only claude` line.
- **Stopping.** Esc on a running call, or `x` in `/tasks` on a backgrounded one, stops the run as Ctrl-C would;
  `cancel` does the same. Anything else - Claude Code's own time limit, the session closing - leaves it running for
  `job` to follow.
- **One at a time.** `just deploy`, `login`, `code`, `destroy`, `init` and `install` take one lock, at a terminal or
  through the server, since two runs could take the same VM ID or race the SSH config. A second is refused with what
  holds it ([During a run](runs.md#one-run-at-a-time)); wait for it.
- **The branch.** A run starts only from `main` with no tracked change, unless you registered with `--any-branch`.
- **The names.** A deploy that would create a VM needs its names: `just deploy`'s default VM is the terminal's, so that
  an agent's forgotten argument creates nothing. `only="configure"`, `only="ssh-config"`, `only="template"` and `check`
  take no names, as at a terminal.

## Register it

```sh
just mcp
```

`just mcp` checks first, and refuses with what to do:

- `claude` is on your `PATH`, and Claude Code is 2.1.309 or later, the oldest the server was tried with;
- this checkout is on `main` with no tracked change, since the settings and the vault are its own (below);
- the vault can be read without a terminal: encrypted, with `.vault_pass` beside it, which `just init` makes, or
  `--vault-password-file` naming where the password is;
- `just install` has put the MCP SDK into the environment.

It then runs `claude mcp add --scope user agent-warehouse`, which has Claude Code's own CLI write the server's entry
into `~/.claude.json`, reads it back with `claude mcp get`, and prints permission rules to paste (below). Sessions
started from then on have the tools, as `mcp__agent-warehouse__<tool>`; `/mcp` in one shows the server. Every value the
server needs is an argument of the command it records - this checkout, its environment's Python, `just`, `uv` and the
`PATH`, `HOME`, `XDG_DATA_HOME` and `CLAUDE_CONFIG_DIR` of the shell you ran `just mcp` in - never an environment
variable, since a project's own `.claude/settings.json` can set those in every server it starts.

| Option | Does |
|---|---|
| `--project-dir <dir>` | registers it for that one project's folder only (Claude Code's local scope) |
| `--any-branch` | lets it run this checkout on any branch, or with tracked changes |
| `--vault-password-file <file>` | where the vault's password is, when it is not `.vault_pass` |
| `--attended 0` or `1` | whether someone is at this machine's screen for the sign-ins a run opens |
| `--browser <command>` | what opens those pages, when the default does not |
| `--show` | prints the command, and an entry for Claude Desktop's `claude_desktop_config.json`, and registers nothing |
| `--remove` | takes the server out again |

Run `just mcp` again when this checkout, its environment, `just`, your `PATH`, `XDG_DATA_HOME` or `CLAUDE_CONFIG_DIR`
moves. After a `git pull` or `just install`, a running server answers every call with "restart this server with
/mcp": its tools are made from the justfile it read when it started.

## Permission rules

Claude Code's permission rules decide what an agent runs without asking you; the project never writes your settings.
In auto mode, without an `ask` rule, its classifier alone decides whether an agent deploys, signs in or opens a VM.
`just mcp` prints these to add to `~/.claude/settings.json`: the tools that only read run unasked, the rest ask, and
an agent that reaches for Bash to run `just destroy` or `just settings` in this checkout itself is asked about first.
`install` asks every time whatever the rules say, in Claude Code's own prompt, and runs for no other client.

```json
"permissions": {
  "allow": ["mcp__agent-warehouse__status", "mcp__agent-warehouse__settings", "mcp__agent-warehouse__logs",
            "mcp__agent-warehouse__log", "mcp__agent-warehouse__jobs", "mcp__agent-warehouse__job"],
  "ask": ["mcp__agent-warehouse__deploy", "mcp__agent-warehouse__login", "mcp__agent-warehouse__code",
          "mcp__agent-warehouse__cancel", "Bash(just destroy:*)", "Bash(just settings:*)"]
}
```

## A VM for a repository

"Use agent-warehouse to make a VM for this project" works from the repository's own folder: the agent reads
`status` for a VM whose checkouts list it already, and opens that one with `code`; otherwise it names a VM after the
repository, checks with `settings` that it will be cloned (by default every repository your GitHub account owns),
`deploy`s it, then opens the repository with `code`. To carry the conversation over, it passes its own session's ID
to `code` - the ID of a conversation of that folder, written in the last minute - and stops working where it was.

## The branch it runs

The settings and the vault are git-ignored files of this one checkout, which other sessions may switch to another
branch. Every result starts by naming the branch and commit the server found, as `on main at 51dacd1`, and a run
starts only from `main` with no tracked change, unless `just mcp --any-branch` allowed any.

## What the agent reads

What a VM printed - its name, its checkouts' branches, a failure's words - is the VM's to choose, and a VM's user runs
an agent of its own. The tools hand it back made plain - no escape sequence, no control or bidi character - with a note
that it is data, never instructions, inside the JSON `status` and `settings` return and at the end of what `logs` and
`log` return. A warning a command printed beside a report it still made, such as `just status` saying that no VPN is
chosen, comes back with it as `warnings`. A result stays under 40 000 characters, cut from the middle: over about 50 000,
Claude Code would save it to a file in the session's folder, which `just code` copies to the VMs. No tool takes or
returns a secret.

How the server is built, and why: [§7.12](../architecture/custom-code.md#712-libclaude_vmmcp--the-mcp-server) and
[Agents through MCP](../architecture/security-model.md#agents-through-mcp).
