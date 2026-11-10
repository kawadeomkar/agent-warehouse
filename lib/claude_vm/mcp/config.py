"""The server's configuration: the arguments of the command just mcp registered, and nothing of its environment.

A project's .claude/settings.json `env` reaches a server Claude Code starts in that project (verified on 2.1.309), so a
variable could be set by any repository the session opens; an argument of the registered command only by the person,
through just mcp. A bad command line never ends the server: it is kept as the refusal every tool answers with.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # The checkout the server runs, its justfile's folder.
    checkout: Path
    # The Python just mcp found there (tools/env-run): every run's PYTHON= and the server's own.
    python: str
    # The just and the uv just mcp ran with, by absolute path: a client's PATH may hold neither.
    just: str
    uv: str
    # The PATH of the shell just mcp ran in: every run's, never the session's.
    path: str
    # Its HOME, and its XDG_DATA_HOME where it set one - where tools/env-run finds the checkout's own folder, the
    # collections Ansible loads among it: every run's, never the session's.
    home: str
    data_home: str = ""
    # Its CLAUDE_CONFIG_DIR where it set one: the Claude Code whose user scope just mcp registered the server in keeps
    # its sessions there, which the code tool's session is looked for in and just code copies.
    claude_config_dir: str = ""
    # Whether someone is at this machine's screen for the sign-ins a run opens: "1" yes, "0" no, "" decided per session.
    attended: str = ""
    # A run on a branch other than main, or with tracked changes, allowed (just mcp --any-branch).
    any_branch: bool = False
    # Where ansible-vault reads the vault's password, when it is not the checkout's .vault_pass.
    vault_password_file: str = ""
    # What opens the sign-in pages, when the default does not (BROWSER).
    browser: str = ""


class _Refused(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # never exit: the server must answer, and say what is wrong
        raise _Refused(message)

    def exit(self, status: int = 0, message: str | None = None):
        raise _Refused(message or "")


def parse(argv: list[str]) -> tuple[Config | None, str]:
    """The configuration, or None and what is wrong with the command line, said as how to fix it."""
    parser = _Parser(prog="tools/mcp", add_help=False, allow_abbrev=False)
    for name in ("checkout", "python", "just", "uv", "path", "home"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--data-home", default="")
    parser.add_argument("--claude-config-dir", default="")
    parser.add_argument("--attended", choices=("", "0", "1"), default="")
    parser.add_argument("--any-branch", action="store_true")
    parser.add_argument("--vault-password-file", default="")
    parser.add_argument("--browser", default="")
    try:
        args = parser.parse_args(argv)
    except _Refused as refused:
        return None, f"the server's registered command is not one just mcp writes ({refused}). Run just mcp again."
    checkout = Path(args.checkout)
    if not checkout.is_absolute() or not (checkout / "justfile").is_file():
        return None, f"{checkout} is not a checkout of agent-warehouse with a justfile. Run just mcp in the checkout."
    return (
        Config(
            checkout=checkout,
            python=args.python,
            just=args.just,
            uv=args.uv,
            path=args.path,
            home=args.home,
            data_home=args.data_home,
            claude_config_dir=args.claude_config_dir,
            attended=args.attended,
            any_branch=args.any_branch,
            vault_password_file=args.vault_password_file,
            browser=args.browser,
        ),
        "",
    )
