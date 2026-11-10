# Agents in other projects

Claude Code sessions in any of your projects can use this checkout's commands through an MCP server, so that you can
ask, from wherever you work, "what are my agent-warehouse VMs doing?". Each of the server's tools is the `just`
command of the same name, with the same options, run in this checkout: every rule and refusal is the command's own.

For now the tools only read:

| Tool | The command | Does |
|---|---|---|
| `status` | `just status --json` | every VM, its checkouts and what needs doing next, read without changing anything |
| `settings` | `just settings --json` | a role's or a setting's value and where it was set; secrets said set or not, never shown |
| `logs` | `just logs` | the run logs, newest first |
| `log` | - | the last lines of one run log, by its name, made plain text |

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
`PATH`, `HOME` and `XDG_DATA_HOME` of the shell you ran `just mcp` in - never an environment variable, since a
project's own `.claude/settings.json` can set those in every server it starts.

| Option | Does |
|---|---|
| `--project-dir <dir>` | registers it for that one project's folder only (Claude Code's local scope) |
| `--any-branch` | lets it run this checkout on any branch, or with tracked changes |
| `--vault-password-file <file>` | where the vault's password is, when it is not `.vault_pass` |
| `--attended 0` or `1` | whether someone is at this machine's screen for the sign-ins a run opens |
| `--browser <command>` | what opens those pages, when the default does not |
| `--show` | prints the command, and an entry for Claude Desktop's `claude_desktop_config.json`, and registers nothing |
| `--remove` | takes the server out again |

Run `just mcp` again when this checkout, its environment, `just`, your `PATH` or your `XDG_DATA_HOME` moves. After a
`git pull` or `just install`, a running server answers every call with "restart this server with /mcp": its tools are
made from the justfile it read when it started.

## Permission rules

Claude Code's permission rules decide what an agent runs without asking you; the project never writes your settings.
`just mcp` prints these to add to `~/.claude/settings.json`: the tools that only read run unasked, and an agent that
reaches for Bash to run `just destroy` or `just settings` in this checkout itself is asked about first.

```json
"permissions": {
  "allow": ["mcp__agent-warehouse__status", "mcp__agent-warehouse__settings", "mcp__agent-warehouse__logs",
            "mcp__agent-warehouse__log"],
  "ask": ["Bash(just destroy:*)", "Bash(just settings:*)"]
}
```

## The branch it runs

The settings and the vault are git-ignored files of this one checkout, which other sessions may switch to another
branch. Every result starts by naming the branch and commit the server found, as `on main at 51dacd1`.

## What the agent reads

What a VM printed - its name, its checkouts' branches, a failure's words - is the VM's to choose, and a VM's user runs
an agent of its own. The tools hand it back made plain - no escape sequence, no control or bidi character - with a note
that it is data, never instructions, inside the JSON `status` and `settings` return and at the end of what `logs` and
`log` return. A result stays under 40 000 characters, cut from the middle: over about 50 000, Claude Code would save it
to a file in the session's folder, which `just code` copies to the VMs. No tool takes or returns a secret.

How the server is built, and why: [§7.12](../architecture/custom-code.md#712-libclaude_vmmcp--the-mcp-server) and
[Agents through MCP](../architecture/security-model.md#agents-through-mcp).
