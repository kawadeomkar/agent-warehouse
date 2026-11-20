"""Where a fleet's files and its state are, for the program that is running (docs/architecture/engine.md §16.9).

One home holds the fleet: `client.yml`, which is one computer's own and never travels; `fleet/`, what a person copies
to a second computer (`hosts/<name>/`, `settings.yml`, `machines.yml`, `vault.age`, `fleet.json`); and, but for the
installed program, the state folder `state/` and the key file `vault-key`. The home is `AGENT_WAREHOUSE_HOME` where it
is set; else a checkout's own `.aw/`, inside that checkout, so that a new worktree has no fleet and changes nothing
until `init` runs there; else, for an installed release, `${XDG_CONFIG_HOME:-~/.config}/agent-warehouse/`, whose state
folder is `${XDG_STATE_HOME:-~/.local/state}/agent-warehouse/` and whose key file is in the data folder,
`${XDG_DATA_HOME:-~/.local/share}/agent-warehouse/vault-key`. A line `fleet_home: <path>` in the default home's
`client.yml` points a checkout at the installed program's home, or the reverse, once and never through a second one.

The state folder and the key file are derived from the home, whichever way it was reached, so that two programs that
drive one fleet share its jobs and its locks and open its vault with one key, and two fleets share neither. The key
file is in neither the fleet's folder, which a person copies or publishes, nor the state folder, which can be made
again and which a cleanup may remove: no folder `state`, `jobs_dir`, `locks_dir`, `logs_dir` or `status_cache`
returns holds it. Only this module and `fleet/vaultkey.py` name it (tests/unit/test_aw_fleet_paths.py).

Standard library only, and it creates, writes and removes nothing: it says where. The folders are made at `DIR_MODE`
and the files at `FILE_MODE` by the code that writes them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# The one variable that moves the home, the state folder and the key file with it. Every test sets it to a directory
# of its own (tests/unit/conftest.py), and the harness passes it on to every run it starts.
VARIABLE = "AGENT_WAREHOUSE_HOME"
# A checkout's home, at its top: git-ignored, and refused by tests/check_no_local_files.sh.
CHECKOUT_HOME = ".aw"
# The folder's name under each of the computer's XDG folders: the installed program's home, state and data.
NAME = "agent-warehouse"
# What this module owns at the top of a home: the computer's own settings, the fleet's files, the state folder, and the
# key that opens the vault.
OWN = ("client.yml", "fleet", "state", "vault-key")
CLIENT, FLEET, STATE, KEY = OWN
# What it owns beneath the state folder, which the workstreams that write there agree on: the jobs, the kernel's
# locks, the run logs and the status cache.
BENEATH_STATE = ("jobs", "locks", "logs", "status")
JOBS, LOCKS, LOGS, STATUS = BENEATH_STATE
# Never this module's. The installed program's home is the folder today's roles write a sign-in's files into on every
# machine the product configured (claude_code_login_dir, github_projects_state_dir): those names are reserved, and
# this module never reads, writes or names one as its own. Code that writes a new file into that folder on a machine
# adds its name here.
RESERVED_ON_A_MACHINE = ("claude-login.*", "gh-login*", "gh-device-login")
# The modes the code that writes a fleet's files makes them at.
DIR_MODE = 0o700
FILE_MODE = 0o600

# client.yml's one line this module reads: the key `fleet_home` at the start of a line, then a path, plain or quoted.
_POINTER = re.compile(r"fleet_home:(?P<rest>(?:[ \t].*)?)")
_VALUE = re.compile(r"""(?:"(?P<double>[^"\\]*)"|'(?P<single>[^']*)'|(?P<plain>[^\s"'#].*?))(?:\s+#.*)?""")
# YAML's null, which sets no pointer; and the first characters YAML reads as something other than a plain string.
_NULL = ("", "~", "null", "Null", "NULL")
_NOT_PLAIN = tuple("[]{},&*!|>%@`")


class Refused(ValueError):
    """Where the fleet is cannot be worked out: the sentence says why and what to do."""


@dataclass(frozen=True)
class Home:
    """The fleet's home and what is derived from it.

    ``root`` holds `client.yml` and `fleet/`; ``state`` is the state folder; ``key_file`` the vault's key. ``how`` is
    how the home was reached: ``"variable"``, ``"checkout"`` or ``"installed"``; ``pointer`` the `client.yml` whose
    `fleet_home` was followed, or None.
    """

    root: Path
    state: Path
    key_file: Path
    how: str
    pointer: Path | None = None


def _user_home(environ: Mapping[str, str]) -> Path:
    given = environ.get("HOME", "")
    if os.path.isabs(given):
        return Path(given)
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _xdg(environ: Mapping[str, str], variable: str, default: str) -> Path:
    """An XDG folder: the variable where it holds an absolute path, else its default under the user's home. A relative
    or empty value is ignored, as the XDG Base Directory specification says."""
    given = environ.get(variable, "")
    return Path(os.path.normpath(given)) if os.path.isabs(given) else _user_home(environ) / default


def installed(environ: Mapping[str, str] | None = None) -> tuple[Path, Path, Path]:
    """The installed program's three folders on this computer: its home (the config folder's), its state folder, and
    the data folder that holds its key file and its releases."""
    env = os.environ if environ is None else environ
    return (
        _xdg(env, "XDG_CONFIG_HOME", ".config") / NAME,
        _xdg(env, "XDG_STATE_HOME", ".local/state") / NAME,
        _xdg(env, "XDG_DATA_HOME", ".local/share") / NAME,
    )


def _absolute(value: str, environ: Mapping[str, str], what: str) -> Path:
    """value as an absolute folder, a leading ~ the user's home."""
    if value == "~" or value.startswith("~/"):
        value = str(_user_home(environ)) + value[1:]
    if not os.path.isabs(value):
        raise Refused(f"{what} is {value!r}, which is not an absolute path: give the folder of the fleet's files whole")
    return Path(os.path.normpath(value))


def _pointer(client: Path, environ: Mapping[str, str]) -> Path | None:
    """The folder client.yml's fleet_home line names, or None where it has none (or no client.yml exists)."""
    try:
        text = client.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise Refused(f"{client} cannot be read ({error}): the fleet's home is not known") from None
    found = [match["rest"].strip() for line in text.splitlines() if (match := _POINTER.fullmatch(line))]
    if len(found) > 1:
        raise Refused(f"{client} says fleet_home more than once: keep the one line that names the fleet's home")
    if not found or not found[0] or found[0].startswith("#"):
        return None
    value = _VALUE.fullmatch(found[0])
    if value is None or found[0].startswith(_NOT_PLAIN):
        raise Refused(f"{client}'s fleet_home is not one plain path: write it as fleet_home: /the/folder")
    if value["plain"] in _NULL:
        return None
    named = next(part for part in (value["double"], value["single"], value["plain"]) if part is not None)
    return _absolute(named, environ, f"{client}'s fleet_home")


def _same(a: Path, b: Path) -> bool:
    return os.path.normpath(a) == os.path.normpath(b)


def _derived(root: Path, how: str, environ: Mapping[str, str], pointer: Path | None = None) -> Home:
    config, state, data = installed(environ)
    if _same(root, config):
        # The installed program's own home, however it was reached: its state and its key are its own folders',
        # never inside the config folder, which people publish.
        return Home(root, state, data / KEY, how, pointer)
    return Home(root, root / STATE, root / KEY, how, pointer)


def kind(program_root: str | os.PathLike) -> str | None:
    """What the program running is: ``"installed"``, a release whose tree holds `release.json` - a release is
    a clone of its tag, so it is told by that file before its `.git`; ``"checkout"``, a tree with a `justfile` and a
    `.git` (a folder, or a worktree's file); None for anything else."""
    program = Path(program_root)
    if (program / "release.json").is_file():
        return "installed"
    if (program / "justfile").is_file() and (program / ".git").exists():
        return "checkout"
    return None


def home(program_root: str | os.PathLike, environ: Mapping[str, str] | None = None) -> Home:
    """The fleet's home for the program at program_root, with its state folder and its key file. Reads at most two
    client.yml files and creates nothing. Raises Refused, with the way out, where it cannot be worked out."""
    env = os.environ if environ is None else environ
    given = env.get(VARIABLE, "")
    if given:
        return _derived(_absolute(given, env, VARIABLE), "variable", env)
    how = kind(program_root)
    if how is None:
        raise Refused(
            f"{program_root} is neither a checkout of agent-warehouse (a justfile and a .git) nor an installed release"
            f" (release.json), so the fleet's home is not known: set {VARIABLE} to its folder"
        )
    default = Path(program_root) / CHECKOUT_HOME if how == "checkout" else installed(env)[0]
    client = default / CLIENT
    target = _pointer(client, env)
    if target is None or _same(target, default):
        return _derived(default, how, env)
    if _pointer(target / CLIENT, env) is not None:
        raise Refused(
            f"{client} points at {target}, whose client.yml points again: fleet_home is followed once, so take one of"
            " the two lines out"
        )
    return _derived(target, how, env, pointer=client)


def fleet_dir(where: Home) -> Path:
    """What a person copies to a second computer: hosts/<name>/, settings.yml, machines.yml, vault.age, fleet.json."""
    return where.root / FLEET


def client_file(where: Home) -> Path:
    """The computer's own settings and its current host, which never travel."""
    return where.root / CLIENT


def state(where: Home) -> Path:
    """What can be made again, which a cleanup may remove: the jobs, the locks, the logs, the status cache."""
    return where.state


def key_file(where: Home) -> Path:
    """The file that opens the vault: in a folder of the computer's own that is neither published nor disposable."""
    return where.key_file


def jobs_dir(where: Home) -> Path:
    return where.state / JOBS


def locks_dir(where: Home) -> Path:
    return where.state / LOCKS


def logs_dir(where: Home) -> Path:
    return where.state / LOGS


def status_cache(where: Home) -> Path:
    return where.state / STATUS


def control_dir(environ: Mapping[str, str] | None = None) -> Path:
    """The one shared control directory of every SSH connection: Ansible's, which today's hop and just status use
    (route.hop_settings' default), short and the same for every fleet on the computer."""
    return _user_home(os.environ if environ is None else environ) / ".ansible" / "cp"
