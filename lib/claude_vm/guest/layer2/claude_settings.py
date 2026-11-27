"""Claude Code's user settings: layer 2's step `claude-settings`, today's roles/claude_code/tasks/settings.yml and the
CLAUDE.md task of its main.yml in plain code (docs/architecture/engine.md §16.10).

Its settings: `claude_code_settings`, the one row of the registry that may hold a secret; the vault's
`anthropic_api_key`, a `Secret` or none; `claude_code_version`; and `claude_code_install_method` and
`claude_code_global_instructions`, the role's options passed through by their own names, each the role's own default
where none is set. What the run writes is today's, decided as settings.yml decides it:

- The managed settings are `claude_code_settings`, the key in its `env` block where one is set, and, natively, the
  update channel (`autoUpdatesChannel`) where `claude_code_version` is stable or latest: merged as Ansible's `combine`
  merges them, mappings key by key, any other value replaced.
- `~/.claude/settings.json` is read as the machine's account (its bytes, and whether its mode is 0600), parsed only
  where something is managed or its text names the key: empty or blank is `{}`, anything but a JSON object fails,
  naming the file. A key an earlier run wrote there, and that no setting manages now, is stale: removed, with the `env`
  block where nothing else is left in it.
- Where something is managed or a key is stale, the file is the settings on the machine with the managed ones merged
  over them, written as Ansible's `to_nice_json(indent=2)` writes it with a newline after, mode 0600, as the account:
  its content on the call's standard input, into a file beside it that is then renamed into place. It is written only
  where its bytes or its mode are other than those.
- `~/.claude/CLAUDE.md` is `claude_code_global_instructions`, as it is (it may hold `{{` and tabs: no template ever
  reads it), mode 0644, written only where the instructions are set and the file is other than they are; empty writes
  nothing and leaves a file already there.

No value of the settings is ever an argument, an event, a difference or a file on this computer: the difference names
the settings that change by their names alone, and the key is added to the run's redaction list before the machine is
read, since the file the run reads holds it. Standard library only.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import json
from collections.abc import Mapping

from claude_vm import text
from claude_vm.guest.layer2.step import Context, Difference, Failed
from claude_vm.secret import Secret
from claude_vm.ssh import runner

# The settings this step reads, by name: the role's options passed through, each with the role's own default where
# none is set (roles/claude_code/defaults/main.yml, held equal by tests/unit/test_aw_claude_settings.py), the rows,
# and the vault's secret.
OPTIONS = {"claude_code_install_method": "native", "claude_code_global_instructions": ""}
ROWS = ("claude_code_settings", "claude_code_version")
SECRET = "anthropic_api_key"
# The key's name in settings.json's env block, and the channels the binary updates itself on.
KEY = "ANTHROPIC_API_KEY"
CHANNELS = ("stable", "latest")
# The files, under the account's home, and the mode each is written with.
SETTINGS = ".claude/settings.json"
INSTRUCTIONS = ".claude/CLAUDE.md"
SETTINGS_MODE = "0600"
INSTRUCTIONS_MODE = "0644"
QUICK = runner.Limits(seconds=30.0, silent=None)

# A file of the account's read: $1 its path under the home, $2 the mode it should have. "absent", "other" (no regular
# file), or whether its mode is that one and then its bytes in base64 on one line.
READ = """set -u
f="$HOME/$1"
if [ ! -e "$f" ]; then echo absent; exit 0; fi
if [ ! -f "$f" ]; then echo other; exit 0; fi
if [ -n "$(find "$f" -prune -perm "$2")" ]; then echo "mode held"; else echo "mode other"; fi
printf 'bytes '
base64 < "$f" | tr -d '\\n'
echo
"""
# A file of the account's written from standard input: $1 its path under the home, $2 its mode. Into a file beside it,
# made by mktemp (0600), then renamed into place, so that no reader sees half of it.
WRITE = """set -eu
umask 077
f="$HOME/$1"
mkdir -p "$(dirname "$f")"
tmp=$(mktemp "$f.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
cat > "$tmp"
chmod "$2" "$tmp"
mv -f -- "$tmp" "$f"
trap - EXIT
"""


def _sh(script: str, *words: str) -> list[str]:
    return ["sh", "-c", script, "sh", *words]


def _failure(result: runner.Result) -> str:
    if isinstance(result, runner.Done):
        said = text.one_line(result.said, 300)
        return f"exit {result.status}" + (f": {said}" if said else "")
    if isinstance(result, runner.Unreachable):
        return "the machine was not reached (ssh's exit 255)"
    if isinstance(result, runner.TimedOut):
        return "it took longer than its time"
    if isinstance(result, runner.Silent):
        return "it said nothing in its time"
    return text.one_line(getattr(result, "why", ""), 300)


@dataclasses.dataclass(frozen=True)
class File:
    """A file of the account's as read: whether it is there, whether its mode is the one it should have, its text."""

    there: bool
    mode_held: bool = False
    content: str = dataclasses.field(default="", repr=False)


async def read_file(ctx: Context, path: str, mode: str) -> File:
    """The file at path under the account's home, read as the account: nothing of it said."""
    result = await ctx.run(_sh(READ, path, mode), limits=QUICK)
    lines = list(result.lines) if isinstance(result, runner.Done) and result.status == 0 else []
    if lines == ["absent"]:
        return File(False)
    if lines == ["other"]:
        raise Failed(f"~/{path} on the machine is no file: move it aside, then re-run")
    if len(lines) != 2 or lines[0] not in ("mode held", "mode other") or not lines[1].startswith("bytes"):
        raise Failed(f"~/{path} could not be read on the machine: {_failure(result)}")
    try:
        content = base64.b64decode(lines[1].removeprefix("bytes").strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise Failed(f"~/{path} on the machine is not text in UTF-8") from None
    return File(True, lines[0] == "mode held", content)


async def write_file(ctx: Context, path: str, mode: str, content: str) -> None:
    """content written to the file at path under the account's home, as the account, on the call's standard input."""
    result = await ctx.run(_sh(WRITE, path, mode), limits=QUICK, stdin=content.encode())
    if not (isinstance(result, runner.Done) and result.status == 0):
        raise Failed(f"~/{path} could not be written on the machine: {_failure(result)}")


def combined(under: Mapping, over: Mapping) -> dict:
    """over merged into under as Ansible's combine(recursive=True) merges: mappings key by key, any other value
    replaced. Neither is changed."""
    merged = {key: _copy(value) for key, value in under.items()}
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = combined(merged[key], value)
        else:
            merged[key] = _copy(value)
    return merged


def _copy(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy(item) for item in value]
    return value


def nice_json(value: object) -> str:
    """value as Ansible's to_nice_json(indent=2) writes it, and the newline settings.yml writes after it."""
    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def api_key(settings: Mapping[str, object]) -> Secret | None:
    """The vault's Anthropic API key in the run's settings, or None where none is set."""
    key = settings.get(SECRET)
    if key is None:
        return None
    if not isinstance(key, Secret):
        raise Failed(f"{SECRET} reaches a step as the vault's Secret, never as plain text")
    return key if key.reveal() else None


def _setting(ctx: Context, name: str) -> object:
    if name in OPTIONS:
        return ctx.settings.get(name, OPTIONS[name])
    if name not in ctx.settings:
        raise Failed(f"the run's settings hold no {name}, which Claude Code's settings read")
    return ctx.settings[name]


def managed(ctx: Context) -> dict:
    """The settings the run manages in settings.json, as settings.yml builds them: see the module's docstring."""
    given = _setting(ctx, "claude_code_settings")
    given = {} if given is None else given
    if not isinstance(given, Mapping) or not all(isinstance(key, str) for key in given):
        raise Failed("claude_code_settings is a mapping of settings.json's settings, by their names")
    try:
        json.dumps(given)
    except (TypeError, ValueError):
        raise Failed("claude_code_settings holds a value JSON cannot write") from None
    wanted = combined({}, given)
    key = api_key(ctx.settings)
    if key is not None:
        wanted = combined(wanted, {"env": {KEY: key.reveal()}})
    method, version = _setting(ctx, "claude_code_install_method"), _setting(ctx, "claude_code_version")
    if method == "native" and version in CHANNELS:
        wanted["autoUpdatesChannel"] = version
    return wanted


def changed_names(now: Mapping, wanted: Mapping, under: str = "") -> list[str]:
    """The settings that differ between now and wanted, each by its dotted name alone: never a value."""
    names = []
    for key in sorted(set(now) | set(wanted)):
        name = f"{under}{key}"
        if key not in wanted:
            names.append(f"{name} removed")
        elif key not in now and isinstance(wanted[key], Mapping) and wanted[key]:
            names.extend(changed_names({}, wanted[key], f"{name}."))
        elif key not in now:
            names.append(f"{name} set")
        elif isinstance(now[key], Mapping) and isinstance(wanted[key], Mapping):
            names.extend(changed_names(now[key], wanted[key], f"{name}."))
        elif now[key] != wanted[key]:
            names.append(f"{name} changed")
    return names


@dataclasses.dataclass(frozen=True)
class Found:
    """What the probe found: each file as read, the text each should be (None where the run writes nothing to it), and
    the settings that change, by name."""

    settings: File
    settings_wanted: str | None = dataclasses.field(repr=False)
    changes: tuple[str, ...]
    instructions: File
    instructions_wanted: str | None = dataclasses.field(repr=False)

    @property
    def settings_due(self) -> bool:
        return _due(self.settings, self.settings_wanted)

    @property
    def instructions_due(self) -> bool:
        return _due(self.instructions, self.instructions_wanted)


def _due(now: File, wanted: str | None) -> bool:
    """Whether the run writes a file: where it has text for it and the file is other than that, or of another mode."""
    return wanted is not None and not (now.there and now.content == wanted and now.mode_held)


def planned(file: File, wanted: Mapping) -> tuple[str | None, tuple[str, ...]]:
    """settings.json's text as the run writes it (None where it writes nothing) and the settings that change."""
    names_key = file.there and KEY in file.content
    if "env" in wanted and wanted["env"] is None:
        # An env of null is no env: what the file's env holds is kept, and the key no setting manages still goes.
        wanted = {name: value for name, value in wanted.items() if name != "env"}
    if not wanted and not names_key:
        return None, ()
    on_disk: object = {}
    if file.there and file.content.strip():
        try:
            on_disk = json.loads(file.content)
        except ValueError:
            on_disk = None
    if not isinstance(on_disk, dict):
        raise Failed(
            f"~/{SETTINGS} on the machine is not a JSON object, and this run has settings to merge into it. Fix the"
            " file or move it aside, then re-run."
        )
    env = on_disk.get("env")
    stale = KEY not in (wanted.get("env") or {}) and names_key and isinstance(env, Mapping) and KEY in env
    if not wanted and not stale:
        return None, ()
    merged = combined(on_disk, wanted)
    if stale:
        now = merged.get("env")
        left = {name: value for name, value in (now.items() if isinstance(now, Mapping) else ()) if name != KEY}
        merged = {name: value for name, value in merged.items() if name != "env"}
        if left:
            merged["env"] = left
    return nice_json(merged), tuple(changed_names(on_disk, merged))


class ClaudeSettings:
    """The step: see the module's docstring."""

    name = "claude-settings"
    part = "claude_code"

    async def probe(self, ctx: Context) -> Found:
        """Both files as read, and what each should be: nothing changed."""
        wanted = managed(ctx)
        key = api_key(ctx.settings)
        if key is not None:
            ctx.job.redact(key)
        instructions = _setting(ctx, "claude_code_global_instructions")
        instructions = "" if instructions is None else instructions
        if not isinstance(instructions, str):
            raise Failed("claude_code_global_instructions is the text of a CLAUDE.md")
        settings = await read_file(ctx, SETTINGS, SETTINGS_MODE)
        settings_wanted, changes = planned(settings, wanted)
        on_disk = await read_file(ctx, INSTRUCTIONS, INSTRUCTIONS_MODE) if instructions else File(False)
        return Found(settings, settings_wanted, changes, on_disk, instructions or None)

    async def apply(self, ctx: Context, state: Found) -> bool:
        if state.settings_due:
            await write_file(ctx, SETTINGS, SETTINGS_MODE, state.settings_wanted or "")
        if state.instructions_due:
            await write_file(ctx, INSTRUCTIONS, INSTRUCTIONS_MODE, state.instructions_wanted or "")
        return state.settings_due or state.instructions_due

    def difference(self, state: Found) -> Difference | None:
        """Each file that would be written, as it is and as it would be, its settings by name alone."""
        what, now, wanted = [], [], []
        if state.settings_due:
            what.append(f"~/{SETTINGS}")
            now.append(_as_found(state.settings, SETTINGS_MODE))
            said = list(state.changes) or ["the same settings, written as the run writes them"]
            if state.settings.there and not state.settings.mode_held:
                said.append(f"mode {SETTINGS_MODE}")
            wanted.append(", ".join(said))
        if state.instructions_due:
            what.append(f"~/{INSTRUCTIONS}")
            now.append(_as_found(state.instructions, INSTRUCTIONS_MODE))
            lines = len((state.instructions_wanted or "").splitlines())
            wanted.append(f"claude_code_global_instructions, {lines} line{'s' if lines != 1 else ''}")
        if not what:
            return None
        return Difference(" and ".join(what), "; ".join(now), "; ".join(wanted))


def _as_found(file: File, mode: str) -> str:
    if not file.there:
        return "absent"
    return "present" + ("" if file.mode_held else f", its mode not {mode}")
