"""`code`: one VM opened in the editor over its SSH alias (card G17; docs/architecture/engine.md §16.4).

The window step of today's playbooks/code.yml in plain code, over today's pure decisions, unchanged: the editor's plan
(editors/registry.py's `editor_open_plan`, each family's), the projects directory and `code`'s own directory on the VM
(sessions.py's `projects_dir` and `just_code_dir`), the alias's write (engine/aliases.py's `one`). The editors are
today's list (`claude_editors`, cli/status_inputs.py's `EDITORS`, held to the inventory defaults by its test).

**Refused before anything is written**, here or on the VM, each in one sentence: an editor `code` cannot open (the
`editor` setting, or `--editor`); the managed SSH file switched off (`claude_ssh_config`); a VM the fleet does not own
(engine/fleet.py's `refusal`, read by a live read that changes nothing), or none of the name; a stopped VM, with the
line that starts it, and one that reports no address; a VM with no host key filed under its identity, with the line that
files it; a home on the VM (the password database's, read as its account) or a projects directory
(`vm_projects_dir`) that is not one plain absolute path; `code`'s own directory (`editor_files_dir`, else
`<projects>/.agent-warehouse`) that is not one, is `/`, is the projects directory, or is the folder opened with the
Claude terminal on; a folder that is not there, said with what the projects directory holds; and `code`'s own
directory that is a link or has one on the way below the projects directory, resolves outside it (`RESOLVED`), or is
inside a checkout (a `.git` in any folder from the first below the projects directory down to it), wherever
anything goes into it. The schema refuses more than one name and a `--project` that is not one folder name before the
handler runs (schema/rules.py's `code.2` and `check.project.2`), and `--session` is refused naming card G18, which
builds it.

**Then, in order**: the alias refreshed (`aliases.one`); the editor found on this computer, by its row's commands on
PATH (each asked `--version` where the row says what it must answer, the first that does taken), and for the family
that reads them (`SETTINGS_FAMILY`) the extension it lacks installed through its own command line (the one write here
beyond the alias) and its settings read, never written, so that the plan says whether `task.allowAutomaticTasks` will
ask; for a plan that writes a task file, its first line read for the marker - and the plan made, and refused where it
refuses; then the writes on the
VM, as its account, every one in `code`'s own directory: that directory where anything goes into it (0755 where new,
one that exists keeping its mode), the plan's directories (the same), the helpers it names (files of playbooks/files/,
copied unchanged, 0644) and the plan's files, each written only where its bytes or its mode differ, into a file beside
it then renamed into place; then the window, the registry's command, started to outlive the run
(runs/children.py's `outlive`), and what the plan says of it reported. Nothing sets a trust, answers an editor's or
Claude Code's security question, or writes a setting of an editor's.

Every command run on the VM is one of `READS` or `WRITES`, by name, each a fixed script or program given its values as
arguments (tests/unit/test_aw_code.py holds it, from what the stand-in machine was sent); every command run here is one
of `HERE`'s, the window's aside. The notes (G19) and the session copies (G18, G18a) are not written here.

**Inside `deploy`** (`window(..., in_deploy=True)`, which card G10 composes into the chain) every failure of the window
is said with the `code` line that opens it later - for a VM found stopped, the deploy that starts it - and stops
nothing: the stage ends skipped.

Standard library, editors/ and the engine's modules at import; asyncio inside the coroutines.
"""

from __future__ import annotations

import dataclasses
import os
import posixpath
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import sessions
from ..cli.status_inputs import EDITORS
from ..editors import registry
from ..editors.common import HELPER_PATH, HELPERS
from ..editors.vscode import HELPERS as FAMILY_HELPERS
from ..editors.zed import TASKS
from ..fleet import hosts, overrides, paths, sealed
from ..guest import layer1
from ..hosts import model
from ..pve.answers import ProxmoxError
from ..runs.stages import DEPLOY, Declared, Stage
from ..schema import printer
from ..schema.printer import said
from ..schema.words import EXITS, Line
from ..ssh import runner
from ..text import one_line, plain_text
from ..vendors.transport import TransportError
from . import aliases, fleet, report, routes
from .configure import home_of

if TYPE_CHECKING:
    from ..runs.attached import Context

PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# The helpers' files, copied unchanged: playbooks/files/.
FILES = PROGRAM_ROOT / "playbooks" / "files"
# deploy's editor stage, and of its steps those this card builds: the sessions' two are G18's, the notes G19's.
STAGE = "editor"
BUILT = ("find", "reach", "folder", "alias", "editor-here", "directory", "window")
_DEPLOYS = DEPLOY.stage(STAGE)
assert _DEPLOYS is not None
DECLARED = Declared(
    "code", (Stage(STAGE, _DEPLOYS.label, steps=tuple(step for step in _DEPLOYS.steps if step.key in BUILT)),)
)
# The NEXT STEPS matter of the window, and of a VM a deploy left stopped.
MATTER = "editor"
STOPPED_MATTER = "vm"
# --session's card.
SESSION_CARD = "G18"
# The helpers any editor's task runs, every one the plan may name: the shared one (code.yml's code_shared_helpers), then
# the family's that starts its terminal (its vars file's code_helpers, its module's HELPERS), held to both by the test.
SHARED_HELPERS = ("claude-start.sh",)
HELPER_NAMES = SHARED_HELPERS + tuple(FAMILY_HELPERS)
START_HELPER = SHARED_HELPERS[0]
# The family whose settings code reads and whose extension it installs; the extension, the one it is given.
SETTINGS_FAMILY = "vscode"
REMOTE_SSH = "ms-vscode-remote.remote-ssh"
# Where that editor keeps its settings, under the person's home, after $VSCODE_APPDATA and the person's configuration
# folder (paths.config_home), which it honours first (its vars file's code_vscode_settings_places).
SETTINGS_PLACES = (
    "Library/Application Support/Code/User/settings.json",
    ".config/Code/User/settings.json",
    "snap/code/current/.config/Code/User/settings.json",
    ".var/app/com.visualstudio.code/config/Code/User/settings.json",
)
# A path with a control character in it, which is never said or handed to the editor.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# A time zone's name, as code.yml takes one from TZ, /etc/localtime's link or /etc/timezone.
ZONE = re.compile(r"[A-Za-z0-9_+-]{1,32}(?:/[A-Za-z0-9_+-]{1,32}){0,3}")
ZONE_PREFIX = re.compile(r"^:?(?:.*/zoneinfo/)?(?:posix/|right/)?")
# The bounds of a command on the VM, and of each of this computer's programs.
QUICK = runner.Limits(seconds=30.0, silent=None)
VERSION_SECONDS = 10.0
EXTENSIONS_SECONDS = 60.0
INSTALL_SECONDS = 300.0
# How long the window's command is watched for an exit that failed before the run goes on without it.
LAUNCH_GRACE = 2.0

# ---------------------------------------------------------------- the commands, each by name ----

# The folder to open, $1, in the projects directory, $2: dir, file or absent (a link followed), then, where it is no
# folder and the projects directory is there, "listed" and each folder it holds, a link to one not counted.
FOLDER = """set -u
if [ -d "$1" ]; then echo dir; exit 0; fi
if [ -e "$1" ]; then echo file; else echo absent; fi
[ -d "$2" ] || exit 0
echo listed
for f in "$2"/*; do
  if [ -d "$f" ] && [ ! -L "$f" ]; then printf '%s\\n' "${f##*/}"; fi
done
"""
# Each folder given that holds a .git, a checkout's or a worktree's (a link there counts), one a line.
CHECKOUTS = """for d; do
  if [ -e "$d/.git" ] || [ -L "$d/.git" ]; then printf '%s\\n' "$d"; fi
done
"""
# The first line of the file $1, after "there", or "absent" where it is no file.
FIRST_LINE = """if [ ! -e "$1" ] && [ ! -L "$1" ]; then echo absent; exit 0; fi
if line=$(head -n 1 -- "$1" 2>/dev/null); then echo there; printf '%s\\n' "$line"; else echo unreadable; fi
"""
# code's own directory $2 as it resolves on the VM, under the projects directory $1: "link" and each of $2 and the
# folders after it (those from the first below the projects directory down to $2) that is a link, "root" and $1
# resolved, and, where $2 is there, "real" and $2 resolved; tab-separated.
RESOLVED = """set -u
root=$1; ours=$2; shift 2
for d in "$ours" "$@"; do
  if [ -L "$d" ]; then printf 'link\\t%s\\n' "$d"; fi
done
printf 'root\\t%s\\n' "$(realpath -e -- "$root" 2>/dev/null)"
if [ -e "$ours" ]; then printf 'real\\t%s\\n' "$(realpath -e -- "$ours" 2>/dev/null)"; fi
"""
# The directory $1, made with mode $2 where it is not there (its parents as mkdir -p makes them), one there keeping its
# own: "there" or "made".
DIRECTORY = """set -eu
if [ -d "$1" ]; then echo there; exit 0; fi
mkdir -p -m "$2" -- "$1"
echo made
"""
# The file $1 given standard input, mode $2: into a file beside it, made by mktemp (0600), then renamed into place,
# so that no reader sees half of it; left where its bytes and its mode are those already: "unchanged" or "written".
FILE = """set -eu
umask 077
tmp=$(mktemp "$1.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
cat > "$tmp"
if [ -f "$1" ] && [ ! -L "$1" ] && cmp -s "$tmp" "$1" && [ -n "$(find "$1" -prune -perm "$2")" ]; then
  echo unchanged
  exit 0
fi
chmod "$2" "$tmp"
mv -f -- "$tmp" "$1"
trap - EXIT
echo written
"""
# Every command code runs on the VM, by name: the reads, and the writes, each in code's own directory alone. The home is
# getent's; every other is one of these scripts, given its values as its arguments.
READS = {
    "home": ("getent", "passwd"),
    "folder": FOLDER,
    "resolved": RESOLVED,
    "checkouts": CHECKOUTS,
    "first-line": FIRST_LINE,
}
WRITES = {"directory": DIRECTORY, "file": FILE}
# Every command code runs here, by name, beside the window's (the registry's own): the editor's command asked its
# version, or its extensions, and the extension installed, which is the list's fifth write.
HERE = {
    "version": ("--version",),
    "extensions": ("--list-extensions",),
    "install": ("--install-extension", REMOTE_SSH),
}


class Failed(Exception):
    """The window not opened: the sentence that says why, with no "error: " before it; refused where nothing was
    written (exit 1), else failed (exit 2); and the matter of NEXT STEPS a deploy says it under."""

    def __init__(self, sentence: str, *, refused: bool = True, matter: str = MATTER) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.refused = refused
        self.matter = matter


@dataclasses.dataclass(frozen=True)
class Asked:
    """What a run of code asks: the VM's name, --project's folder ("" for the whole projects directory), and --editor's
    key ("" for the editor setting's)."""

    name: str
    project: str = ""
    editor: str = ""


def _values(values: Mapping[str, object]) -> dict[str, object]:
    return {name: getattr(value, "value", value) for name, value in values.items()}


def editor_of(asked: Asked, settings: Mapping[str, object]) -> str:
    """The key of the editor that opens the window: --editor's, else the setting's. Failed for one code cannot open,
    said by where it came from."""
    setting = str(settings.get("editor") or "")
    key = asked.editor or setting
    if key not in EDITORS:
        given = (
            f'--editor is "{key}"'
            if asked.editor
            else said(
                'The editor setting is "{key}" ({settings} says where it is set)',
                {"key": key, "settings": Line("settings", {"name": "editor"}, host_flag="")},
            )
        )
        raise Failed(f"{given}, which code cannot open: it opens {' or '.join(EDITORS)}.")
    return key


def before(asked: Asked, settings: Mapping[str, object]) -> str:
    """What is refused before anything is read: the editor (editor_of) and the managed SSH file switched off. The
    editor's key."""
    key = editor_of(asked, settings)
    if settings.get("claude_ssh_config") is False:
        raise Failed(
            "claude_ssh_config is false, so this project keeps no SSH alias for the VM and has nothing to hand"
            f" {EDITORS[key]['name']}. Set it true, or open the VM by address yourself."
        )
    return key


def zone(environ: Mapping[str, str], localtime: str = "/etc/localtime", named: str = "/etc/timezone") -> str:
    """The time zone the Claude terminal says times in: TZ, then the zone /etc/localtime links into a zoneinfo directory
    for, then /etc/timezone where it is a file; a zone's name alone, else UTC (code.yml's code_timezone)."""
    found = [environ.get("TZ", ""), os.path.realpath(localtime) if os.path.lexists(localtime) else ""]
    if os.path.isfile(named):
        try:
            found.append(Path(named).read_text(errors="replace"))
        except OSError:
            pass
    for each in found:
        name = ZONE_PREFIX.sub("", each.strip(), count=1)
        if ZONE.fullmatch(name):
            return name
    return "UTC"


def which(names: Sequence[str], environ: Mapping[str, str], every: bool) -> list[str]:
    """What `which` (every: `which -a`) prints for names on environ's PATH: each name's programs in the order of PATH,
    the first alone unless every."""
    folders = [folder for folder in str(environ.get("PATH", "")).split(os.pathsep) if folder]
    found: list[str] = []
    for name in names:
        for folder in folders:
            path = os.path.join(folder, name)
            if os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.X_OK):
                found.append(path)
                if not every:
                    break
    return list(dict.fromkeys(found))


def editor_settings(settings: Mapping[str, object], environ: Mapping[str, str]) -> dict[str, object]:
    """Where the person's settings of SETTINGS_FAMILY's editor are and what they hold, read and never written: its
    settings file's setting, else the first place the editor itself would put them that is there; the text None where
    it could not be read."""
    given = str(settings.get("vscode_user_settings_file") or "")
    home = str(environ.get("HOME", ""))
    places = [f"{environ['VSCODE_APPDATA']}/Code/User/settings.json"] if environ.get("VSCODE_APPDATA") else []
    if home.startswith("/"):
        places.append(str(paths.config_home(environ) / "Code" / "User" / "settings.json"))
        places += [f"{home}/{place}" for place in SETTINGS_PLACES]
    path = os.path.expanduser(given) if given else next((place for place in places if os.path.exists(place)), "")
    exists = bool(path) and os.path.exists(path)
    text: str | None = ""
    if exists:
        try:
            text = Path(path).read_text(errors="replace")
        except OSError:
            text = None
    return {"settings_file": path, "settings_exists": exists, "settings_text": text}


def below(files_dir: str, root: str) -> list[str]:
    """The folders from the first below the projects directory down to code's own, by name: none for one outside it."""
    ours, top = posixpath.normpath(files_dir), posixpath.normpath(root)
    return ours[len(top) + 1 :].split("/") if ours.startswith(top.rstrip("/") + "/") else []


def home_checked(lines: Sequence[str], user: str, name: str) -> str:
    """user's home on the VM name, from getent's line: Failed where it is none, or no plain absolute path."""
    try:
        home = home_of(lines, user)
    except ValueError as error:
        raise Failed(f"{user}'s home on {name} could not be read: {error}.") from None
    if CONTROL.search(home):
        raise Failed(
            f"{user}'s home on {name} is no plain absolute path, so code neither says it nor hands it to the editor."
            " Set vm_projects_dir to the projects directory there instead."
        )
    return home


def _plain_absolute(path: str) -> bool:
    return path.startswith("/") and not CONTROL.search(path)


def _why(result: runner.Result) -> str:
    """Why a command on the VM did not answer, as one line."""
    if isinstance(result, runner.Done):
        return one_line(result.said, 300) or f"it ended with exit {result.status}"
    return one_line(getattr(result, "said", "") or getattr(result, "why", "") or "it did not answer", 300)


class Opening:
    """One window's run: the VM found, reached, its folder settled, its alias written, the editor's plan made and
    applied, and the window opened, each a step of the run's editor stage."""

    def __init__(self, ctx: Context, host: fleet.Host, asked: Asked, key: str) -> None:
        self.ctx, self.host, self.asked, self.key = ctx, host, asked, key
        self.editor = EDITORS[key]
        self.settings = _values(host.values)
        self.machine: str | None = None
        self.step_open = ""
        self.target: fleet.Target | None = None
        self.reached: runner.Target | None = None

    # ------------------------------------------------------------ what is said ----

    @property
    def editor_name(self) -> str:
        return str(self.editor["name"])

    def again(self) -> Line:
        """The code line that opens this window again: its --project, and --editor where it is not the setting's."""
        values: dict[str, object] = {"name": self.asked.name}
        if self.asked.project:
            values["project"] = self.asked.project
        if self.asked.editor and self.asked.editor != str(self.settings.get("editor") or ""):
            values["editor"] = self.asked.editor
        return Line("code", values, host_flag=self.host.host_flag)

    def start(self) -> Line:
        return Line("deploy", {"names": [self.asked.name], "only": "provision", "start": True},
                    host_flag=self.host.host_flag)  # fmt: skip

    def step(self, key: str, outcome: str | None = None, why: str = "") -> None:
        """A step of the editor stage begun, or ended so, for the VM (the run's own before it is found)."""
        extra: dict[str, object] = {"stage": STAGE}
        if outcome is not None:
            extra["outcome"] = outcome
        if why:
            extra["why"] = why
        self.step_open = "" if outcome is not None else key
        self.ctx.emit("step", self.machine, key, **extra)

    def failed(self, why: str) -> None:
        """The stage failed for the VM, its open step with it, and the VM's end; before the VM was found, the run's own
        stage, which ends the step open in it."""
        if self.machine is None:
            self.ctx.emit("stage", None, STAGE, outcome="failed", why=why)
            return
        if self.step_open:
            self.step(self.step_open, "failed", why)
        self.ctx.emit("stage", self.machine, STAGE, outcome="failed", why=why)
        self.ctx.emit("end", self.machine, why, outcome="failed")

    # ------------------------------------------------------------ the machine's commands ----

    async def on_machine(self, name: str, words: Sequence[str], stdin: bytes | None = None) -> runner.Result:
        """The command READS or WRITES names, given words, run on the VM as its account: the one way code runs one."""
        assert self.reached is not None
        if name in READS:
            fixed = READS[name]
        elif name in WRITES:
            fixed = WRITES[name]
        else:
            raise ValueError(f"{name} is no command code runs on a VM")
        command = [*fixed, *words] if isinstance(fixed, tuple) else ["sh", "-c", fixed, "sh", *words]
        return await runner.run(self.ctx.job, self.reached, command, stdin, QUICK, dict(self.host.environ))

    def ssh_failed(self, result: runner.Result, reach: str) -> Failed:
        """SSH to the VM failed: said with what ssh said, and how to see why."""
        user = self.target.user if self.target is not None else ""
        return Failed(
            f"{self.asked.name} is running at {reach}, but SSH to it as {user} failed, so {self.editor_name} could not"
            f" connect either: {_why(result)}. A VM that has just booted can need another minute; `ssh"
            f" {self.asked.name}` shows why.",
            refused=False,
        )

    async def read(self, name: str, words: Sequence[str], reach: str) -> runner.Done:
        result = await self.on_machine(name, words)
        if not isinstance(result, runner.Done):
            raise self.ssh_failed(result, reach)
        return result

    async def write(self, name: str, words: Sequence[str], stdin: bytes | None = None) -> None:
        result = await self.on_machine(name, words, stdin)
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(
                f"{words[0]} on {self.asked.name} could not be written: {_why(result)}.".replace("..", "."),
                refused=False,
            )

    # ------------------------------------------------------------ this computer's commands ----

    async def here(self, name: str, program: str, seconds: float) -> tuple[int | None, str, str]:
        """program given HERE's words of name, as a child of the run's: its exit status (None past seconds), what it
        printed and what it said, plain."""
        import asyncio

        from ..runs import children

        argv = [program, *HERE[name]]
        pipe = asyncio.subprocess.PIPE
        try:
            process = await children.start(
                self.ctx.job,
                argv,
                children.GROUP,
                dict(self.host.environ),
                asyncio.subprocess.DEVNULL,
                pipe,
                pipe,
                children.PROGRAM,
            )
        except OSError as error:
            return 127, "", f"{program}: {error.strerror or error}"
        try:
            out, err = await asyncio.wait_for(process.communicate(), seconds)
        except TimeoutError:
            runner._kill(process)
            await process.wait()
            return None, "", f"no answer within {seconds:g} s"
        except BaseException:
            runner._kill(process)
            raise
        return process.returncode, plain_text(out.decode(errors="replace")), plain_text(err.decode(errors="replace"))

    # ------------------------------------------------------------ the steps ----

    async def find(self) -> fleet.Target:
        """The VM from a live read that changes nothing: the fleet's own, running, with an address and its key filed."""
        import asyncio

        name = self.asked.name
        try:
            found = await asyncio.to_thread(fleet.requested, self.host, (name,), change=False)
        except fleet.Refused as refused:
            self.step("find")
            raise Failed(refused.sentence) from None
        for line in found.said:
            report.say(self.ctx, None, "fleet", line, "")
        [target] = found.targets
        self.target = target
        self.machine = target.identity
        self.ctx.emit("stage", self.machine, STAGE, name=name)
        self.step("find")
        why = fleet.refusal(target)
        if why:
            raise Failed(f"Not opening {name}: {why}")
        if not target.running:
            raise Failed(
                said(
                    "{name} is stopped, so there is nothing to connect to: {start} starts it; then run this again,"
                    " {again}.",
                    {"name": name, "start": self.start(), "again": self.again()},
                ),
                matter=STOPPED_MATTER,
            )
        if not target.address:
            raise Failed(
                said(
                    "{name} is running and reported no address, so there is nothing to connect to: {status} shows the"
                    " fleet; wait for its guest agent, and run this again.",
                    {"name": name, "status": Line("status", {}, host_flag=self.host.host_flag)},
                )
            )
        try:
            layer1.key_filed(runner.KNOWN_HOSTS, target.identity, name, self.host.host_flag)
        except layer1.Refused as refused:
            raise Failed(f"{name}: {one_line(str(refused), None)}") from None
        self.step("find", "done")
        return target

    async def reach(self, target: fleet.Target) -> tuple[routes.Route, aliases.Rules, aliases.Alias, str, str]:
        """The route settled and the VM's Target taken from it, its account's home read from the password database
        where vm_projects_dir does not say the projects directory outright: the route, the alias's rules and record
        (its workspace still empty), where the editor reaches the VM (the alias's HostName) and the home ("" unread)."""
        self.step("reach")
        try:
            route = await routes.settle(self.ctx, self.host)
            [reached] = await route.targets([target])
            await route.ensure(reached)
        except routes.Refused as refused:
            raise Failed(f"{self.asked.name}: {refused.sentence}") from None
        self.reached = reached
        rules = aliases.Rules.of(self.host, (target.name,), through=route.through)
        alias = aliases.Alias(target.name, target.address, target.user, target.identity)
        where = aliases.hostname(alias, rules)
        home = ""
        if not str(self.settings.get("vm_projects_dir") or ""):
            done = await self.read("home", [target.user], where)
            if done.status != 0:
                raise self.ssh_failed(done, where)
            home = home_checked(done.lines, target.user, target.name)
        self.step("reach", "done")
        return route, rules, alias, where, home

    async def folder(self, home: str, where: str) -> dict[str, Any]:
        """The folder to open and code's own directory, settled and held to their rules, and looked for on the VM: the
        projects directory (root), the folder (target), code's directory (files_dir) and the folders below the projects
        directory down to it (below), the Claude terminal (terminal), and whether anything goes into code's directory
        (writes) and whether the editor's task does (task_written)."""
        self.step("folder")
        name = self.asked.name
        root = sessions.projects_dir(self.settings.get("vm_projects_dir"), home)
        project = self.asked.project
        target = root + (f"/{project}" if project else "")
        files_dir = sessions.just_code_dir(root, self.settings.get("editor_files_dir") or "")
        terminal = self.settings.get("editor_claude_terminal") is not False
        claude = str(self.editor.get("claude_terminal") or "none")
        under = below(files_dir, root)
        helper = posixpath.join(files_dir, HELPERS, START_HELPER)
        task_written = terminal and claude == "listed" and bool(under) and bool(HELPER_PATH.fullmatch(helper))
        writes = (terminal and claude == "started") or task_written
        sessions_on = self.settings.get("claude_sessions_sync") is not False
        if not _plain_absolute(root):
            raise Failed(
                f'vm_projects_dir is a path on the VM, and must be one plain absolute one, not "{plain_text(root)}":'
                f" {self.editor_name} is handed it as written."
            )
        if terminal or sessions_on or writes:
            if not _plain_absolute(files_dir):
                raise Failed(
                    f"editor_files_dir is a path on the VM, and must be one plain absolute one, not"
                    f' "{plain_text(files_dir)}": {self.editor_name} is handed it as written.'
                )
            if posixpath.normpath(files_dir) == "/":
                raise Failed(
                    "editor_files_dir is /, the whole of the VM: name a directory of its own for the files code keeps."
                )
            if posixpath.normpath(files_dir) == posixpath.normpath(root):
                raise Failed(
                    f"editor_files_dir is the projects directory, {root}, whose folders are the projects; name a"
                    " directory of its own."
                )
            if terminal and posixpath.normpath(files_dir) == posixpath.normpath(target):
                raise Failed(f"{files_dir} on {name} is where code keeps its own files, not a project.")
        found = await self.read("folder", [target, root], where)
        lines = list(found.lines)
        if found.status != 0 or not lines:
            raise self.ssh_failed(found, where)
        if lines[0] != "dir":
            raise Failed(self._missing(lines, root, target))
        folders = [posixpath.join(root, *under[:depth]) for depth in range(1, len(under) + 1)]
        if writes:
            await self.resolved(root, files_dir, folders, where)
        if writes and under:
            checkouts = await self.read("checkouts", folders, where)
            if checkouts.status != 0:
                raise self.ssh_failed(checkouts, where)
            if checkouts.lines:
                raise Failed(
                    f"{files_dir}, code's own directory on {name} (editor_files_dir), is inside the checkout"
                    f" {plain_text(checkouts.lines[0])}: code writes beside the repositories, never in one. Set"
                    " editor_files_dir to a directory outside every checkout."
                )
        self.step("folder", "done")
        return {
            "root": root,
            "target": target,
            "files_dir": files_dir,
            "below": under,
            "terminal": terminal,
            "writes": writes,
            "task_written": task_written,
        }

    async def resolved(self, root: str, files_dir: str, folders: Sequence[str], where: str) -> None:
        """code's own directory read as it resolves on the VM: Failed where it, or a folder on the way to it below the
        projects directory, is a link, or where one written inside the projects directory resolves outside it."""
        name = self.asked.name
        found = await self.read("resolved", [root, files_dir, *folders], where)
        fields = [line.split("\t", 1) for line in found.lines]
        if found.status != 0 or any(len(each) != 2 for each in fields):
            raise self.ssh_failed(found, where)
        links = [value for key, value in fields if key == "link"]
        if links:
            raise Failed(
                f"{plain_text(links[0])} on {name} is a link, on the way to code's own directory (editor_files_dir,"
                f" {files_dir}): code writes only in a directory of its own, never where a link leads. Remove the"
                " link, or set editor_files_dir to a directory that is none."
            )
        real = dict(fields)
        if (
            folders
            and real.get("real")
            and real.get("root")
            and not posixpath.normpath(real["real"]).startswith(posixpath.normpath(real["root"]).rstrip("/") + "/")
        ):
            raise Failed(
                f"{files_dir}, code's own directory on {name}, resolves to {plain_text(real['real'])}, outside the"
                f" projects directory {root}: code writes only in a directory of its own there."
            )

    def _missing(self, lines: Sequence[str], root: str, target: str) -> str:
        """Why the folder is not there, with what the projects directory holds (code.yml's "Fail when the folder is not
        there")."""
        name, project = self.asked.name, self.asked.project
        listed = "listed" in lines
        names = sorted(plain_text(each) for each in lines[lines.index("listed") + 1 :]) if listed else []
        configure = Line("deploy", {"names": [name], "only": "configure"}, host_flag=self.host.host_flag)
        if project and lines[0] == "file":
            return f"{target} on {name} is a file, not a project folder."
        if project and names:
            close = [each for each in names if each.lower() == project.lower()]
            hint = (
                f"Did you mean {' or '.join(close)}? Folder names are case-sensitive."
                if close
                else f"The projects there are: {', '.join(names)}."
            )
            return f"There is no project named {project} in {root} on {name}. {hint}"
        if project and listed:
            return said("{root} on {name} holds no projects yet. {configure} clones your repositories into it.",
                        {"root": root, "name": name, "configure": configure})  # fmt: skip
        return said(
            "{root} does not exist on {name} yet: the VM has not been configured, or vm_projects_dir points somewhere"
            " else. {configure} creates it and clones your repositories into it.",
            {"root": root, "name": name, "configure": configure},
        )

    async def alias(self, route: routes.Route, rules: aliases.Rules, target: fleet.Target, root: str) -> None:
        self.step("alias")
        assert self.reached is not None
        try:
            await aliases.one(self.ctx, self.host, route, target, self.reached, rules, root)
        except aliases.Refused as refused:
            raise Failed(refused.sentence) from None
        self.step("alias", "done")

    async def probe(self, settled: Mapping[str, Any], where: str) -> dict[str, object]:
        """What the editor's check finds here, and of the editor's own on the VM: its command (which), each one's
        version where the row asks it, the editor's settings, and its task file's first line."""
        candidates = [str(each) for each in self.editor.get("cli_candidates") or ()]
        every = bool(self.editor.get("version_re"))
        found = which(candidates, self.host.environ, every)
        probe: dict[str, object] = {"which": found}
        if every:
            # Each asked in which's order until one says what the row asks: pick_cli takes that first one, and no
            # command after it is started at all.
            versions = []
            for path in found:
                status, out, _ = await self.here("version", path, VERSION_SECONDS)
                if status == 0:
                    versions.append([path, out.rstrip("\n")])
                    if re.match(str(self.editor["version_re"]), versions[-1][1]):
                        break
            probe["versions"] = versions
        if self.editor.get("family") == SETTINGS_FAMILY:
            probe.update(editor_settings(self.settings, self.host.environ))
        if settled["task_written"]:
            probe["tasks_head"] = await self.tasks_head(settled, where)
        return probe

    async def tasks_head(self, settled: Mapping[str, Any], where: str) -> str | None:
        """The first line of the editor's task file in code's directory, None where nothing is there; one that is there
        but cannot be read is someone else's ("", no marker), left and said by the plan, never taken for absent."""
        head = await self.read("first-line", [posixpath.join(settled["files_dir"], TASKS)], where)
        lines = list(head.lines)
        if head.status != 0 or lines[:1] not in (["absent"], ["there"], ["unreadable"]):
            raise self.ssh_failed(head, where)
        if lines[0] == "absent":
            return None
        return lines[1] if lines[0] == "there" and len(lines) > 1 else ""

    def plan(self, target: fleet.Target, settled: Mapping[str, Any], where: str, probe: Mapping[str, object]) -> dict:
        """The editor's plan of the window, from code.yml's inputs, as the registry returns it: unchanged."""
        setting = str(self.settings.get("editor") or "")
        flag = f" --editor {self.key}" if self.key != setting else ""
        inputs = {
            "vm": {"name": target.name, "vmid": target.vmid},
            "target": settled["target"],
            "root": settled["root"],
            "files_dir": settled["files_dir"],
            "project": self.asked.project,
            "reach": where,
            "terminal": settled["terminal"],
            "notes": False,
            "task_written": settled["task_written"],
            "editor_flag": flag,
            "host_flag": f" --host {self.host.host_flag}" if self.host.host_flag else "",
            "helpers": list(HELPER_NAMES),
            "probe": dict(probe),
            "remote_control": self.settings.get("claude_code_remote_control") is not False,
            "github_signed_in": self.settings.get("github_projects_auth") is not False
            and str(self.settings.get("github_projects_auth_method") or "auto") != "token",
            "clone_protocol": str(self.settings.get("github_projects_clone_protocol") or "https"),
            "login_scopes": ",".join(str(each) for each in self.settings.get("github_projects_login_scopes") or ()),
            "remote_control_unit": str(
                self.settings.get("claude_code_remote_control_service") or "claude-remote-control"
            ),
            "timezone": zone(self.host.environ),
        }
        try:
            return registry.editor_open_plan(self.key, EDITORS, inputs)
        except ValueError as error:
            raise Failed(f"{self.editor_name}'s window could not be planned: {one_line(str(error), 300)}") from None

    async def editor_here(self, target: fleet.Target, settled: Mapping[str, Any], where: str) -> dict:
        """The editor on this computer, and the plan of its window, refused where it refuses; given the
        extension it lacks, for SETTINGS_FAMILY's."""
        self.step("editor-here")
        probe = await self.probe(settled, where)
        plan = self.plan(target, settled, where, probe)
        if plan["refusals"]:
            raise Failed(" ".join(plan["refusals"]))
        if self.editor.get("family") == SETTINGS_FAMILY:
            program = plan["launch"][0]
            status, out, err = await self.here("extensions", program, EXTENSIONS_SECONDS)
            if status != 0:
                raise Failed(f"`{program} --list-extensions` failed: {one_line(err, 300) or f'exit {status}'}.",
                             refused=False)  # fmt: skip
            if REMOTE_SSH not in [line.strip().lower() for line in out.splitlines()]:
                status, _, err = await self.here("install", program, INSTALL_SECONDS)
                if status != 0:
                    why = one_line(err, 300) or f"exit {status}"
                    raise Failed(f"`{program} --install-extension {REMOTE_SSH}` failed: {why}.", refused=False)
        self.step("editor-here", "done")
        return plan

    async def directory(self, settled: Mapping[str, Any]) -> None:
        """code's own directory on the VM, where anything goes into it: 0755 where new, one there keeping its mode."""
        self.step("directory")
        if settled["writes"]:
            await self.write("directory", [settled["files_dir"], "0755"])
        self.step("directory", "done")

    async def window(self, settled: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
        """The plan applied - its directories, the helpers it names and its files, in code's own directory - and the
        window opened by the registry's command, then what the plan says of it."""
        import asyncio
        import time

        from ..runs import children

        self.step("window")
        files_dir = settled["files_dir"]
        writes = list(plan["vm_writes"])
        for each in writes:
            if each["kind"] == "directory":
                await self.write("directory", [posixpath.join(files_dir, each["path"]), each["mode_if_new"]])
        for name in HELPER_NAMES:
            if name in plan["helpers"]:
                await self.write(
                    "file", [posixpath.join(files_dir, HELPERS, name), "0644"], (FILES / name).read_bytes()
                )
        for each in writes:
            if each["kind"] == "file":
                path = posixpath.join(files_dir, each["path"])
                await self.write("file", [path, each["mode"]], each["content"].encode())
        launch = [str(word) for word in plan["launch"]]
        try:
            process = children.outlive(self.ctx.job, launch, dict(self.host.environ))
        except OSError as error:
            raise Failed(f"`{launch[0]}` could not be started: {error.strerror or error}.", refused=False) from None
        end = time.monotonic() + LAUNCH_GRACE
        while process.poll() is None and time.monotonic() < end:
            await asyncio.sleep(0.05)
        if process.returncode not in (None, 0):
            raise Failed(f"`{launch[0]}` ended with exit {process.returncode}, so no window opened.", refused=False)
        text = " ".join(each for each in (*plan["report"], *plan["report_end"]) if each).strip()
        report.say(self.ctx, self.machine, MATTER, text, "")
        self.step("window", "done")

    async def run(self) -> None:
        target = await self.find()
        route, rules, alias, where, home = await self.reach(target)
        settled = await self.folder(home, where)
        await self.alias(route, rules, target, settled["root"])
        plan = await self.editor_here(target, settled, where)
        await self.directory(settled)
        await self.window(settled, plan)


async def window(ctx: Context, host: fleet.Host, asked: Asked, *, in_deploy: bool = False) -> bool:
    """asked's VM opened in the editor, its editor stage run: True once the window opened. Failed for what stopped it,
    said by the stage's end; inside deploy (in_deploy) said instead with the line that opens it later, and False, the
    stage skipped and nothing else stopped."""
    try:
        opening = Opening(ctx, host, asked, before(asked, _values(host.values)))
    except Failed as failed:
        if not in_deploy:
            raise
        report.say(ctx, None, MATTER, failed.sentence, "")
        return False
    try:
        await opening.run()
    except Failed as failed:
        why = one_line(failed.sentence, None)
        if in_deploy:
            later = opening.start() if failed.matter == STOPPED_MATTER else opening.again()
            text = (
                said(
                    "{name} is stopped, and this deploy left it so: it was not configured, signed in or opened in"
                    " {editor}. To start it and finish the deploy: {line}",
                    {"name": asked.name, "editor": opening.editor_name, "line": later},
                )
                if failed.matter == STOPPED_MATTER
                else said(
                    "{why} {editor} was not opened; to open it later: {line}",
                    {"why": why, "editor": opening.editor_name, "line": later},
                )
            )
            if opening.step_open:
                opening.step(opening.step_open, "skipped")
            report.say(ctx, opening.machine, failed.matter, text,
                       printer.line(later.verb, later.values, refused=False, host_flag=later.host_flag))  # fmt: skip
            ctx.emit("stage", opening.machine, STAGE, outcome="skipped")
            return False
        opening.failed(why)
        raise
    except (ProxmoxError, TransportError) as error:
        failed = Failed(f"{host.name} could not be read: {fleet.sentence(error)}", refused=False)
        opening.failed(failed.sentence)
        raise failed from None
    ctx.emit("stage", opening.machine, STAGE, outcome="done")
    ctx.emit("end", opening.machine, "opened", outcome="succeeded")
    return True


def _refused(sentence: str) -> int:
    print(f"error: {one_line(sentence, None).removeprefix('error: ')}", file=sys.stderr)
    return EXITS["refused"]


def run(given: Any, **values: object) -> int:
    """code's handler (words.py's row): --session refused naming its card, the editor and the managed file refused
    before the run, then the window as its run, under the VM's lock. The exit status: 0 once the window opened, 1 for a
    refusal before anything was written, 2 for a failure after, 130 for a run a person cancelled."""
    if values.get("session"):
        return _refused(said("{line} is not built yet (card {card} builds it)",
                             {"line": Line("code", {"name": values.get("name"), "session": values["session"]},
                                           host_flag=""), "card": SESSION_CARD}))  # fmt: skip
    environ = dict(os.environ)
    asked = Asked(str(values.get("name") or ""), str(values.get("project") or ""), str(values.get("editor") or ""))
    words = tuple(str(word) for word in getattr(given, "set", ()) or ())
    try:
        home = paths.home(PROGRAM_ROOT, environ)
        layout = hosts.Layout(home, environ)
        reached = hosts.reach(layout, model.Request(host=str(values.get("host") or ""), names=(asked.name,)))
        many = layout.many()
        for note in reached.notes:
            print(f"warning: {note.text}" if note.level == "warning" else note.text, file=sys.stderr)
        given_values = overrides.run_values(words, secrets=(), environ=environ)
        settings = overrides.resolve(home, reached.host.name, given_values, environ=environ)
        before(asked, _values(settings))
    except (hosts.Refused, overrides.Refused, paths.Refused, ValueError) as error:
        return _refused(str(error))
    except Failed as failed:
        return _refused(failed.sentence)

    from .. import runs
    from ..runs import live, locks, textlog

    host_name = reached.host.name
    detached = getattr(given, "detached_run", False) is True
    starter = {"kind": "detach" if detached else "terminal"}
    verbosity = getattr(given, "verbosity", 0)
    host_flag = None if values.get("host") else host_name if many else ""
    title = printer.line("code", values, refused=True, host_flag=host_flag)

    async def body(ctx: Context) -> int:
        import asyncio

        live.Live(
            ctx.job,
            DECLARED,
            verbosity=verbosity if isinstance(verbosity, int) else 0,
            cancelled=ctx.cancelled,
            title=title,
            host=host_name,
        )
        try:
            host = await asyncio.to_thread(fleet.open_for_run, home, reached, words, environ)
        except (fleet.Refused, overrides.Refused, sealed.Refused, paths.Refused) as error:
            return _refused(fleet.sentence(error))
        with host:
            try:
                await window(ctx, host, asked)
            except Failed as failed:
                if ctx.cancelled.is_set():
                    return EXITS["cancelled"]
                return EXITS["refused"] if failed.refused else EXITS["failed"]
        return EXITS["done"]

    draft = runs.Draft(
        paths.jobs_dir(home),
        "code",
        title,
        host_name,
        [],
        starter,
        True,
        values=values,
        lock_machines=[(host_name, asked.name)],
        lock_policy=locks.policy_of(str(settings["run_lock"].value)),
        logs=textlog.logs(home, given),
    )
    return runs.run(draft, body)
