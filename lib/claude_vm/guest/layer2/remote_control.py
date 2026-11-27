"""Remote Control on a machine: layer 2's step `remote-control`, today's roles/claude_code/tasks/remote_control.yml and
the removal of its units in main.yml, in plain code (docs/architecture/engine.md §16.10). It runs after
`claude-settings`, whose settings.json it reads.

**Wanted or not** is decided once, by `remote_control_wanted(settings)`, today's `claude_code_remote_control_wanted`:
`claude_code_remote_control` is true and no Anthropic API key is set, since a key outranks the claude.ai login Remote
Control needs. Where it is not wanted, each of its units that is installed, the path unit first, is stopped, disabled
and removed, and systemd made to forget it; where a key is what turned it off, the report says so.

**Where it is wanted**, the state is read as the machine's account, nothing changed: Claude Code's binary (the native
launcher, `~/.local/bin/claude`, or under npm the `claude` its PATH finds), `claude auth status` (the login, never
anything it holds said), settings.json (for what in it disables Remote Control: an env key of `BLOCKING_ENV`,
`apiKeyHelper`, an `ANTHROPIC_BASE_URL` whose host is not api.anthropic.com), `~/.claude.json` (whether the three
prompts are answered), the sessions' directory, both units' files and what systemd says of each. From those, as
today's vars/main.yml decides: **eligible** where the account is signed in through claude.ai, no `apiKeySource` is
reported (any at all: a Console login reports claude.ai too) and nothing in settings.json disables it; the **path
unit** wanted only while the login is the one thing missing (no login, no blocker). The step then, in today's order:

1. makes the sessions' directory (mode 0755, as the account where it is under the home, else as root);
2. answers onboarding, workspace trust for that directory and Remote Control's own prompt in `~/.claude.json`, only
   where one is unanswered (Claude Code owns the file and rewrites it);
3. installs the service's unit, rendered here from today's template (`claude-remote-control.service.j2`) with jinja2
   alone, as Ansible's template module renders it (held byte for byte to it by a test, since a unit whose text changed
   is restarted);
4. eligible with `spawn: worktree`, requires a git repository in the directory;
5. removes the path unit where it is not wanted;
6. eligible: enables the service and starts it, restarted where its unit changed or it is not active; where it was
   (re)started, it must still be running, under the same main PID and with no restart, `STAY_UP` seconds later; then
   says Remote Control is live;
7. the path unit wanted: installs it, from `claude-remote-control.path.j2` (`PathChanged=` on the credentials file,
   never `PathExists=`, which would restart the service whenever it stopped), and enables and starts it, so that a
   login made any other way starts the service;
8. not eligible: leaves the service disabled and stopped, and says what is in the way and what to run.

Every outcome emits its report and its NEXT STEPS record, under `remote_control`, and under `claude` the sign-in
where none is stored and none follows in the run (`claude_code_sign_in_follows`). This step starts no sign-in.

The units' folder is `/etc/systemd/system` (a test gives one of its machine's own); systemctl runs as root through
the machine's sudo, every unit file is written as root from the call's standard input, and a value of the settings
that is no single line is refused before anything is rendered, since it would add lines to a unit. What the run sets
for itself (`RUN_SET`): the machine's name, the host its lines name, and whether a sign-in follows. Standard library;
jinja2 is imported where a unit is rendered.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import re
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from claude_vm import sessions, text
from claude_vm.guest.layer2 import claude_settings
from claude_vm.guest.layer2.step import Context, Difference, Failed
from claude_vm.schema import printer
from claude_vm.ssh import runner

# The settings this step reads, by name: the role's options passed through, each with the role's own default where
# none is set (held equal to roles/claude_code/defaults/main.yml by tests/unit/test_aw_claude_settings.py), and the
# rows; the vault's key is claude_settings.SECRET.
OPTIONS = {"claude_code_install_method": "native", "claude_code_remote_control_service": "claude-remote-control"}
ROWS = (
    "claude_code_remote_control",
    "claude_code_remote_control_name",
    "claude_code_remote_control_dir",
    "claude_code_remote_control_permission_mode",
    "claude_code_remote_control_spawn",
)
# What the run sets for itself, each with its value where it sets none: the machine's name (required), the host its
# lines name ("" where the fleet has one), and whether the run's sign-in follows this configure.
RUN_SET = {"vm_name": None, "host_flag": "", "claude_code_sign_in_follows": False}
SPAWNS = ("same-dir", "worktree", "session")
# The env keys of settings.json that disable Remote Control (today's claude_code_remote_control_blocking_env, held
# equal to the role's vars by the test), and the one host an ANTHROPIC_BASE_URL may name.
BLOCKING_ENV = (
    "DISABLE_TELEMETRY",
    "DO_NOT_TRACK",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "DISABLE_GROWTHBOOK",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)
API_HOST = "api.anthropic.com"
UNITS = "/etc/systemd/system"
TEMPLATES = Path(__file__).resolve().parents[4] / "roles" / "claude_code" / "templates"
# What Ansible's template module gives ansible_managed, which each unit's first line says.
MANAGED = "Ansible managed"
# How long a server just started must stay up (today's claude_code_remote_control_stay_up_seconds).
STAY_UP = 20.0
CLAUDE_JSON = ".claude.json"
QUICK = runner.Limits(seconds=30.0, silent=None)
SHOWN = ("LoadState", "ActiveState", "SubState", "UnitFileState", "ExecMainPID", "NRestarts")
# A line that is a form, never the command to run (engine/report.py's, which guest/ cannot import).
PLACEHOLDER = re.compile(r"(?<!\S)<[a-z][a-z0-9_-]*>")

# Each machine's script, run by sh -c with its words as $1, $2 and on.
HOME = 'printf "%s\\n" "$HOME"'
# Claude Code's binary ($1 the method) and `claude auth status` run by it: "bin <path>" ("bin " for none), then what
# it said, then "status <its exit>".
AUTH = """set -u
if [ "$1" = native ]; then b="$HOME/.local/bin/claude"; [ -x "$b" ] || b=
else b=$(command -v claude) || b=; fi
printf 'bin %s\\n' "$b"
[ -n "$b" ] || exit 0
"$b" auth status 2>/dev/null
printf '\\nstatus %s\\n' "$?"
"""
# A file read by its path, $1: "absent", or its bytes in base64 on one line.
CAT = """set -u
if [ ! -f "$1" ]; then echo absent; exit 0; fi
printf 'bytes '
base64 < "$1" | tr -d '\\n'
echo
"""
# The sessions' directory, $1: "absent", "other" (no directory) or whether its mode is 0755; then whether .git is in it.
DIRECTORY = """set -u
if [ ! -e "$1" ]; then echo absent; elif [ ! -d "$1" ]; then echo other
elif [ -n "$(find "$1" -prune -perm 0755)" ]; then echo "mode held"; else echo "mode other"; fi
if [ -e "$1/.git" ]; then echo "git yes"; else echo "git no"; fi
"""
MAKE_DIRECTORY = 'mkdir -p -- "$1" && chmod 0755 -- "$1"'
# A unit's file written from standard input, $1 its path: into a file beside it, then renamed into place.
INSTALL = """set -eu
umask 022
tmp=$(mktemp "$1.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
cat > "$tmp"
chmod 0644 "$tmp"
mv -f -- "$tmp" "$1"
trap - EXIT
"""


def remote_control_wanted(settings: Mapping[str, object]) -> bool:
    """Whether Remote Control is wanted on the machine: claude_code_remote_control, and no Anthropic API key set
    (today's claude_code_remote_control_wanted). The one place it is decided."""
    return settings.get("claude_code_remote_control") is True and claude_settings.api_key(settings) is None


def blockers(on_disk: Mapping) -> list[str]:
    """What in settings.json disables Remote Control, by name, as today's vars/main.yml lists it."""
    env = on_disk.get("env") if isinstance(on_disk.get("env"), Mapping) else {}
    found = [name for name in env if name in BLOCKING_ENV]
    if "apiKeyHelper" in on_disk:
        found.append("apiKeyHelper")
    if "ANTHROPIC_BASE_URL" in env:
        try:
            host = urllib.parse.urlsplit(str(env["ANTHROPIC_BASE_URL"])).hostname
        except ValueError:
            host = None
        if host != API_HOST:
            found.append("ANTHROPIC_BASE_URL")
    return found


def eligible(status: Mapping, blocked: list[str]) -> bool:
    """Whether Remote Control can start: signed in through claude.ai, no API key source of any kind, nothing in
    settings.json in the way."""
    return (
        status.get("loggedIn") is True
        and status.get("authMethod") == "claude.ai"
        and not status.get("apiKeySource")
        and not blocked
    )


def render(template: str, values: Mapping[str, object]) -> str:
    """A unit's template rendered as Ansible's template module renders it: trim_blocks, the trailing newline kept,
    systemd_arg the filter the role's plugin gives (lib/claude_vm/text.py)."""
    import jinja2

    environment = jinja2.Environment(
        trim_blocks=True, keep_trailing_newline=True, undefined=jinja2.StrictUndefined, autoescape=False
    )
    environment.filters["systemd_arg"] = text.systemd_arg
    return environment.from_string((TEMPLATES / template).read_text()).render(values)


def _sh(script: str, *words: str) -> list[str]:
    return ["sh", "-c", script, "sh", *words]


def _failure(result: runner.Result) -> str:
    return claude_settings._failure(result)


def _bytes(result: runner.Result, what: str) -> str | None:
    lines = list(result.lines) if isinstance(result, runner.Done) and result.status == 0 else []
    if lines == ["absent"]:
        return None
    if len(lines) != 1 or not lines[0].startswith("bytes"):
        raise Failed(f"{what} could not be read on the machine: {_failure(result)}")
    try:
        return base64.b64decode(lines[0].removeprefix("bytes").strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise Failed(f"{what} on the machine is not text in UTF-8") from None


def _one_line(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or text.plain_text(value) != value or "\n" in value or "\t" in value:
        raise Failed(f"{name} is one line of text with no control character, as a systemd unit holds it")
    return value


@dataclasses.dataclass(frozen=True)
class Unit:
    """One of Remote Control's units as read: its file's path and text (None where absent), what systemd says of it,
    and the text the run wants in it (None where it wants none)."""

    name: str
    path: str
    text: str | None = dataclasses.field(repr=False)
    shown: Mapping[str, str]
    wanted: str | None = dataclasses.field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.shown.get("ActiveState") == "active"

    @property
    def enabled(self) -> bool:
        return self.shown.get("UnitFileState") == "enabled"


@dataclasses.dataclass(frozen=True)
class Found:
    """What the probe found, and what the step does about it: its actions in order, and its outcome's report and
    NEXT STEPS lines."""

    wanted: bool
    service: Unit
    path: Unit
    actions: tuple[tuple[str, ...], ...]
    report: str
    lines: Mapping[str, str]
    seed: str | None = dataclasses.field(default=None, repr=False)
    stay_up: bool = False


class RemoteControl:
    """The step: see the module's docstring. units, the folder of systemd's units; stay_up, how long a server just
    started must stay up."""

    name = "remote-control"
    part = "claude_code"

    def __init__(self, *, units: str = UNITS, stay_up: float = STAY_UP) -> None:
        self.units = units.rstrip("/") or "/"
        self.stay_up = stay_up

    # ------------------------------------------------------------ the settings ----

    @staticmethod
    def _setting(ctx: Context, name: str) -> object:
        if name in OPTIONS:
            return ctx.settings.get(name, OPTIONS[name])
        if name in RUN_SET and RUN_SET[name] is not None:
            return ctx.settings.get(name, RUN_SET[name])
        if name not in ctx.settings:
            raise Failed(f"the run's settings hold no {name}, which Remote Control reads")
        return ctx.settings[name]

    def _lines(self, ctx: Context) -> dict[str, str]:
        name = _one_line("vm_name", self._setting(ctx, "vm_name"))
        host = self._setting(ctx, "host_flag")
        host = "" if host is None else str(host)

        def line(verb: str, values: Mapping[str, object]) -> str:
            made = printer.line(verb, {"names": [name], **values}, refused=False, host_flag=host)
            if made.splitlines() != [made] or PLACEHOLDER.search(made):
                raise Failed(f"the line {made!r} is no command a NEXT STEPS record holds")
            return made

        return {
            "name": name,
            "login": line("login", {"only": "claude"}),
            "configure": line("deploy", {"only": "configure"}),
            "code": printer.line("code", {"name": name}, refused=False, host_flag=host),
        }

    # ------------------------------------------------------------ the probe ----

    async def _unit(self, ctx: Context, service: str, kind: str) -> Unit:
        unit = f"{service}.{kind}"
        path = f"{self.units}/{unit}"
        content = _bytes(await ctx.run(_sh(CAT, path), limits=QUICK), path)
        result = await ctx.run(["systemctl", "show", unit, f"--property={','.join(SHOWN)}"], limits=QUICK)
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(f"systemd's state of {unit} could not be read on the machine: {_failure(result)}")
        shown = {}
        for said in result.lines:
            key, equals, value = said.partition("=")
            if equals and key in SHOWN:
                shown[key] = value.strip()
        return Unit(unit, path, content, shown)

    async def probe(self, ctx: Context) -> Found:
        """What is on the machine, and what the step does about it: nothing changed."""
        service_name = self._setting(ctx, "claude_code_remote_control_service")
        service_name = _one_line("claude_code_remote_control_service", service_name)
        if not re.fullmatch(r"[A-Za-z0-9:_.-]+", service_name):
            raise Failed("claude_code_remote_control_service is a systemd unit's name, of letters, digits and :_.-")
        lines = self._lines(ctx)
        service = await self._unit(ctx, service_name, "service")
        path = await self._unit(ctx, service_name, "path")
        if not remote_control_wanted(ctx.settings):
            return self._off(ctx, service, path, lines)
        return await self._on(ctx, service, path, lines)

    def _off(self, ctx: Context, service: Unit, path: Unit, lines: Mapping[str, str]) -> Found:
        actions = tuple(("remove", unit.name, unit.path) for unit in (path, service) if unit.text is not None)
        name = lines["name"]
        if claude_settings.api_key(ctx.settings) is not None and ctx.settings.get("claude_code_remote_control") is True:
            report = (
                f"Remote Control was not set up on {name}: an Anthropic API key is configured, and ANTHROPIC_API_KEY"
                " outranks the claude.ai login that Remote Control requires, so the server would refuse to start."
                " Clear the vault's anthropic_api_key and re-run to use Remote Control instead."
            )
        else:
            report = f"Remote Control is off on {name}: claude_code_remote_control is false."
        return Found(False, service, path, actions, report, {"remote_control": ""})

    async def _on(self, ctx: Context, service: Unit, path: Unit, lines: Mapping[str, str]) -> Found:
        account = _one_line("the machine's account", ctx.target.account)
        home = await ctx.run(_sh(HOME), limits=QUICK)
        if not (isinstance(home, runner.Done) and home.status == 0 and len(home.lines) == 1 and home.lines[0]):
            raise Failed(f"the account's home could not be read on the machine: {_failure(home)}")
        home_dir = _one_line("the account's home", home.lines[0].rstrip("/") or "/")
        given = self._setting(ctx, "claude_code_remote_control_dir")
        target = sessions.projects_dir("" if given is None else given, home_dir)
        target = _one_line("claude_code_remote_control_dir", target)
        if target == home_dir:
            raise Failed(
                "claude_code_remote_control_dir is the home directory itself, which Claude Code never saves workspace"
                " trust for: name another"
            )
        spawn = self._setting(ctx, "claude_code_remote_control_spawn")
        if spawn not in SPAWNS:
            raise Failed(f"claude_code_remote_control_spawn is one of {', '.join(SPAWNS)}")
        title = _one_line("claude_code_remote_control_name", self._setting(ctx, "claude_code_remote_control_name"))
        mode = self._setting(ctx, "claude_code_remote_control_permission_mode")
        mode = _one_line("claude_code_remote_control_permission_mode", mode)
        method = self._setting(ctx, "claude_code_install_method")

        # Claude Code's binary and its login.
        auth = await ctx.run(_sh(AUTH, str(method)), limits=QUICK)
        said = list(auth.lines) if isinstance(auth, runner.Done) and auth.status == 0 else []
        if not said or not said[0].startswith("bin "):
            raise Failed(f"Claude Code's login could not be read on the machine: {_failure(auth)}")
        binary = said[0].removeprefix("bin ").strip()
        if not binary:
            raise Failed(
                f"`claude` is not on {account}'s PATH, so no systemd unit can start it: Claude Code's install"
                " (claude-install) puts it there"
            )
        binary = _one_line("Claude Code's binary", binary)
        ended = said[-1] if len(said) > 1 and said[-1].startswith("status ") else "status ?"
        answer = "\n".join(said[1:-1]).strip()
        status: Mapping = {}
        if ended == "status 0" and answer.startswith("{"):
            try:
                parsed = json.loads(answer)
            except ValueError:
                parsed = {}
            status = parsed if isinstance(parsed, dict) else {}

        # settings.json, and what in it is in the way.
        settings = await claude_settings.read_file(ctx, claude_settings.SETTINGS, claude_settings.SETTINGS_MODE)
        on_disk: object = {}
        if settings.there and settings.content.strip():
            try:
                on_disk = json.loads(settings.content)
            except ValueError:
                on_disk = None
        if not isinstance(on_disk, dict):
            raise Failed(
                f"~/{claude_settings.SETTINGS} on the machine is not a JSON object: fix the file or move it aside, then"
                " re-run"
            )
        blocked = blockers(on_disk)
        can_start = eligible(status, blocked)
        signed_in = status.get("loggedIn") is True
        bridge = not signed_in and not blocked

        # The three prompts.
        config = await claude_settings.read_file(ctx, CLAUDE_JSON, claude_settings.SETTINGS_MODE)
        answered: object = {}
        if config.there and config.content.strip():
            try:
                answered = json.loads(config.content)
            except ValueError:
                answered = None
        if not isinstance(answered, dict):
            raise Failed(
                f"~/{CLAUDE_JSON} on the machine is not a JSON object: fix the file or move it aside, then re-run"
            )
        projects = answered.get("projects") if isinstance(answered.get("projects"), Mapping) else {}
        trusted = projects.get(target) if isinstance(projects.get(target), Mapping) else {}
        seed = None
        if not (
            answered.get("hasCompletedOnboarding")
            and answered.get("remoteDialogSeen")
            and trusted.get("hasTrustDialogAccepted")
        ):
            seeded = {
                "hasCompletedOnboarding": True,
                "remoteDialogSeen": True,
                "projects": {target: {"hasTrustDialogAccepted": True}},
            }
            # As the role writes it: to_nice_json(indent=2), with no newline after.
            seed = claude_settings.nice_json(claude_settings.combined(answered, seeded)).removesuffix("\n")

        # The sessions' directory.
        folder = await ctx.run(_sh(DIRECTORY, target), limits=QUICK)
        found = list(folder.lines) if isinstance(folder, runner.Done) and folder.status == 0 else []
        if len(found) != 2 or found[0] not in ("absent", "other", "mode held", "mode other"):
            raise Failed(f"{target} could not be read on the machine: {_failure(folder)}")
        if found[0] == "other":
            raise Failed(f"{target} on the machine is no directory, and Remote Control's sessions open there")

        values = {
            "ansible_managed": MANAGED,
            "claude_code_user": account,
            "claude_code_home": home_dir,
            "claude_code_remote_control_bin": binary,
            "claude_code_remote_control_target_dir": target,
            "claude_code_remote_control_name": title,
            "claude_code_remote_control_permission_mode": mode,
            "claude_code_remote_control_spawn": spawn,
            "claude_code_remote_control_service": service.name.removesuffix(".service"),
        }
        try:
            service = dataclasses.replace(service, wanted=render("claude-remote-control.service.j2", values))
            if bridge:
                path = dataclasses.replace(path, wanted=render("claude-remote-control.path.j2", values))
        except ValueError as error:
            raise Failed(f"Remote Control's unit could not be made: {error}") from None

        # What the step does, in today's order.
        actions: list[tuple[str, ...]] = []
        if found[0] != "mode held":
            under = target.startswith(home_dir.rstrip("/") + "/")
            actions.append(("directory", target, "account" if under else "root"))
        if seed is not None:
            actions.append(("seed", CLAUDE_JSON))
        changed = service.text != service.wanted
        if changed:
            actions.append(("install", service.name, service.path))
        if can_start and spawn == "worktree" and found[1] != "git yes":
            raise Failed(
                f'claude_code_remote_control_spawn is "worktree", which creates each session as a git worktree, but'
                f" {target} on {lines['name']} is not a git repository, so the server would exit at once. Put a"
                " repository there first (github_projects can clone one into it), or set"
                " claude_code_remote_control_spawn to same-dir or session."
            )
        if not bridge and path.text is not None:
            actions.append(("remove", path.name, path.path))
        if changed:
            actions.append(("reload",))
        stay_up = False
        if can_start:
            if not service.enabled:
                actions.append(("enable", service.name))
            if changed or not service.active:
                actions.append(("restart", service.name))
                stay_up = True
            plan = str(status.get("subscriptionType") or "unknown")
            report = (
                f'Remote Control is live for {lines["name"]} as "{title}" ({plan} plan). Open claude.ai/code or the'
                " Claude mobile app, signed in as the same account, and it appears in the session list."
            )
            nexts = {"remote_control": ""}
        else:
            if bridge:
                path_changed = path.text != path.wanted
                if path_changed:
                    actions.append(("install", path.name, path.path))
                    actions.append(("reload",))
                if not (path.enabled and path.active):
                    actions.append(("watch", path.name))
            if service.enabled or service.active:
                actions.append(("stop", service.name))
            report, nexts = self._not_started(ctx, account, status, blocked, bridge, lines)
        return Found(True, service, path, tuple(actions), report, nexts, seed, stay_up)

    def _not_started(
        self, ctx: Context, account: str, status: Mapping, blocked: list[str], bridge: bool, lines: Mapping[str, str]
    ) -> tuple[str, dict[str, str]]:
        """What is in the way of Remote Control and what to run, as today's role says them, and the NEXT STEPS
        records."""
        name = lines["name"]
        signed_in = status.get("loggedIn") is True
        if blocked:
            blocker = (
                f"~/.claude/settings.json for {account} on it disables Remote Control through {', '.join(blocked)}."
                " Each switches off the feature-flag evaluation Remote Control depends on, or routes the client away"
                " from the claude.ai login it needs. Remove it from claude_code_settings and from the file on the"
                " machine, or set claude_code_remote_control: false to leave Remote Control off."
            )
        elif not signed_in:
            blocker = f"no Claude Code login is stored for {account} on it."
        elif status.get("apiKeySource"):
            source = text.one_line(str(status.get("apiKeySource")), 100)
            blocker = (
                f'Claude Code on it uses an API key from "{source}", which outranks the claude.ai login Remote'
                " Control needs. Remove it wherever it is set (the shell environment, an env block in a settings"
                ' file, apiKeyHelper), or run "claude auth logout" if it came from a Console login.'
            )
        else:
            method = text.one_line(str(status.get("authMethod") or "unknown"), 100)
            blocker = (
                f'it is signed in through "{method}", but Remote Control needs a claude.ai login on a Pro, Max, Team'
                " or Enterprise plan. Remove what selects that method wherever it is set - CLAUDE_CODE_OAUTH_TOKEN"
                ' for a token from "claude setup-token", or a CLAUDE_CODE_USE_* setting that routes it to Bedrock,'
                ' Vertex AI or another provider - or run "claude auth logout" for a stored login.'
            )
        follows = self._setting(ctx, "claude_code_sign_in_follows") is True
        if not signed_in:
            then = (
                "deploy signs it in next, in this run."
                if follows
                else f'Sign in once: "{lines["login"]}" opens the sign-in page on your machine and asks for the code'
                " the page shows" + (", and starts it." if not blocked else ".")
            )
            if bridge:
                then += (
                    f" A sign-in made any other way, such as in the Claude terminal of {lines['code']}, starts it by"
                    f' itself too; "{lines["configure"]}" then confirms it.'
                )
        elif blocked:
            then = ""
        else:
            then = (
                f'"{lines["login"]}" checks again and signs it in if a login is still needed; then re-run'
                f' "{lines["configure"]}" and the service starts.'
            )
        report = f"Remote Control is configured on {name} but not started: {blocker} {then}".rstrip()
        if signed_in:
            nexts = {"remote_control": lines["configure"] if blocked else lines["login"]}
        else:
            nexts = {"remote_control": "", "claude": "" if follows else lines["login"]}
        return report, nexts

    # ------------------------------------------------------------ the apply ----

    async def _root(self, ctx: Context, command: list[str], what: str, stdin: bytes = b"") -> None:
        result = await ctx.run(command, as_root=True, limits=QUICK, stdin=stdin)
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(f"{what} failed on the machine: {_failure(result)}")

    async def _show(self, ctx: Context, unit: str) -> Mapping[str, str]:
        result = await ctx.run(["systemctl", "show", unit, f"--property={','.join(SHOWN)}"], limits=QUICK)
        shown = {}
        for said in result.lines if isinstance(result, runner.Done) else ():
            key, equals, value = said.partition("=")
            if equals and key in SHOWN:
                shown[key] = value.strip()
        return shown

    async def apply(self, ctx: Context, state: Found) -> bool:
        units = {state.service.name: state.service, state.path.name: state.path}
        started: Mapping[str, str] = {}
        for action in state.actions:
            kind = action[0]
            if kind == "directory":
                await self._directory(ctx, action[1], action[2] == "root")
            elif kind == "seed":
                await claude_settings.write_file(ctx, CLAUDE_JSON, claude_settings.SETTINGS_MODE, state.seed or "")
            elif kind == "install":
                content = (units[action[1]].wanted or "").encode()
                await self._root(ctx, _sh(INSTALL, action[2]), f"installing {action[1]}", content)
            elif kind == "remove":
                await self._root(ctx, ["systemctl", "disable", "--now", action[1]], f"stopping {action[1]}")
                await self._root(ctx, ["rm", "-f", "--", action[2]], f"removing {action[1]}")
                await self._root(ctx, ["systemctl", "daemon-reload"], "systemd's reload")
            elif kind == "reload":
                await self._root(ctx, ["systemctl", "daemon-reload"], "systemd's reload")
            elif kind == "enable":
                await self._root(ctx, ["systemctl", "enable", action[1]], f"enabling {action[1]}")
            elif kind == "restart":
                await self._root(ctx, ["systemctl", "restart", action[1]], f"starting {action[1]}")
                started = await self._show(ctx, action[1])
            elif kind == "watch":
                await self._root(ctx, ["systemctl", "enable", "--now", action[1]], f"starting {action[1]}")
            elif kind == "stop":
                await self._root(ctx, ["systemctl", "disable", "--now", action[1]], f"stopping {action[1]}")
        if state.stay_up:
            await self._stayed_up(ctx, state.service.name, started)
        self._say(ctx, state)
        return bool(state.actions)

    async def _directory(self, ctx: Context, target: str, as_root: bool) -> None:
        result = await ctx.run(_sh(MAKE_DIRECTORY, target), as_root=as_root, limits=QUICK)
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(f"{target} could not be made on the machine: {_failure(result)}")

    async def _stayed_up(self, ctx: Context, unit: str, started: Mapping[str, str]) -> None:
        """The server just started still running, under the same main PID and with no restart, STAY_UP later."""
        import asyncio

        await asyncio.sleep(self.stay_up)
        after = await self._show(ctx, unit)
        pid = after.get("ExecMainPID", "0")
        if (
            after.get("ActiveState") == "active"
            and after.get("SubState") == "running"
            and pid not in ("", "0")
            and pid == started.get("ExecMainPID")
            and after.get("NRestarts") == started.get("NRestarts")
        ):
            return
        seconds = f"{self.stay_up:g}"
        raise Failed(
            f"{unit} did not stay running: {seconds} seconds after starting it is {after.get('ActiveState', 'unknown')}"
            f"/{after.get('SubState', 'unknown')}, with main PID {pid or '?'} (was {started.get('ExecMainPID', '?')})"
            f" and {after.get('NRestarts', '?')} restarts (was {started.get('NRestarts', '?')}). Read the reason on"
            f" the machine with sudo journalctl -u {unit} -n 40"
        )

    @staticmethod
    def _say(ctx: Context, state: Found) -> None:
        """The outcome's report, then its NEXT STEPS records, by engine/report.py's say: Remote Control's matter first,
        and the sign-in's beside it where the report records that too, as today's set_stats records both."""
        # Here alone: aw status reads remote_control_wanted from this module, and cli/ never imports the engine
        # (tests/unit/test_aw_cli_imports.py).
        from claude_vm.engine import report

        (matter, line), *rest = state.lines.items()
        report.say(ctx.job, ctx.machine, matter, state.report, line, also=dict(rest))

    # ------------------------------------------------------------ the difference ----

    def difference(self, state: Found) -> Difference | None:
        """What the step would do, in its order, and the units as they are now."""
        if not state.actions:
            return None
        said = []
        for action in state.actions:
            kind = action[0]
            if kind == "directory":
                said.append(f"{action[1]} made")
            elif kind == "seed":
                said.append(f"~/{CLAUDE_JSON}: onboarding, workspace trust and Remote Control's prompt answered")
            elif kind == "install":
                said.append(f"{action[1]} installed")
            elif kind == "remove":
                said.append(f"{action[1]} stopped, disabled and removed")
            elif kind == "enable":
                said.append(f"{action[1]} enabled")
            elif kind == "restart":
                said.append(f"{action[1]} started")
            elif kind == "watch":
                said.append(f"{action[1]} enabled and started")
            elif kind == "stop":
                said.append(f"{action[1]} stopped and disabled")
        now = "; ".join(_as_now(unit) for unit in (state.service, state.path))
        return Difference("Remote Control", now, "; ".join(said))


def _as_now(unit: Unit) -> str:
    if unit.text is None:
        return f"{unit.name} absent"
    shown = unit.shown
    state = f"{shown.get('ActiveState', 'unknown')}/{shown.get('SubState', 'unknown')}"
    return f"{unit.name} {state}, {shown.get('UnitFileState', 'unknown')}"
