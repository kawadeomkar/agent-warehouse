"""Claude Code brought to the release the settings name: layer 2's step `claude-install`, every way Claude Code is
installed (docs/architecture/engine.md §16.10).

Its settings: `claude_code_install_method` (native, the default, or npm) and `claude_code_download_url`, the role's
options passed through by their own names, each the role's own default where none is set; `claude_code_version`
(stable, latest or a version); and, for the release's documents on this computer, `tls_ca_file` and `network_proxy`.
What it does is today's role's, task by task (roles/claude_code/tasks/install_native.yml and install_npm.yml), which
stay as they are until the cutover.

**Native**, the default. The probe runs `~/.local/bin/claude --version` as the machine's account: nothing answering is
no install. A channel, stable or latest, installs only where none is (the binary updates itself on its channel, which
the settings' step writes); an exact version installs where the version installed is another. To install, the release
is settled on this computer through vendors/downloads.py's `claude_release`, which reads the channel's pointer, the
release's manifest and Anthropic's detached signature of it, each held to its cap, verifies the signature with the
vendored key (vendors/keys.py's, found by its fingerprint) and returns the version and the SHA-256 of each platform's
binary only once it has: this module never verifies a signature itself. The machine's platform is read (`uname -m`, as
the role maps it: x86_64 linux-x64, aarch64 linux-arm64), and a release that names no binary for it is refused. Then
three calls of the runner as the machine's account: the binary downloaded with the machine's `curl` (https alone, its
time bounded) into the account's own `~/.claude/downloads`, never /tmp, which may be mounted noexec, and its SHA-256
said back, held here to the manifest's; its digest read once more there and, only where it is the manifest's, the
binary's own `install` of that exact release run (never a channel's name, which it could resolve and fetch again for
itself), bounded on the machine with `timeout`, and the SHA-256 of what `~/.local/bin/claude` then resolves to said
back, held here to the same digest; and the download removed, whatever happened. The installer script is never used:
it verifies no signature.

**npm**: as today's install_npm.yml, as root through the machine's sudo: `npm --version` must answer; `npm ls -g
--depth=0 --json` is read for `@anthropic-ai/claude-code`; where it is absent it is installed with `npm install -g`,
given the version only where `claude_code_version` is an exact one, so that stable and latest install once and an exact
version moves it, as today's `state: present` does; and what npm then lists is held to what was asked. A version is held
to stable, latest or a release's pattern before anything runs: npm installs any spec given as a version - another
package, a repository, a tarball's address - as root.

The difference names the release installed and the one wanted. Standard library and the vendors' downloads; asyncio
is imported inside the coroutines.
"""

from __future__ import annotations

import dataclasses
import json
import re

from claude_vm import text
from claude_vm.guest.layer2.step import Context, Difference, Failed
from claude_vm.ssh import runner
from claude_vm.vendors import downloads, keys, proxy, trust

# The settings this step reads, by name: the role's options passed through, each with the role's own default where
# none is set (roles/claude_code/defaults/main.yml, held equal by tests/unit/test_aw_claude_install.py), and the rows.
OPTIONS = {
    "claude_code_install_method": "native",
    "claude_code_download_url": "https://downloads.claude.ai/claude-code-releases",
}
ROWS = ("claude_code_version", "tls_ca_file", "network_proxy")
METHODS = ("native", "npm")
CHANNELS = downloads.CHANNELS
# A release's version: the pattern the channels name one by (today's claude_code_release_pattern).
RELEASE = downloads.RELEASE
# The machine's platform by `uname -m`, as the role maps it; any other is named as it is, and the manifest decides.
PLATFORMS = {"x86_64": "linux-x64", "aarch64": "linux-arm64"}
# npm's package.
PACKAGE = "@anthropic-ai/claude-code"
# The launcher, under the account's home.
LAUNCHER = ".local/bin/claude"

# The calls' bounds: a read; the download, whose curl is bounded to 120 s on the machine, today's get_url's timeout;
# the install, bounded to INSTALL_SECONDS there; npm's install, bounded to NPM_SECONDS there.
QUICK = runner.Limits(seconds=30.0, silent=None)
DOWNLOAD_SECONDS = 120
INSTALL_SECONDS = 300
NPM_SECONDS = 600
SLOW = runner.Limits(seconds=float(NPM_SECONDS + 30), silent=None)

# Each machine's script, run by sh -c with its words as $1, $2 and on: no value of the settings or of a release is
# ever part of a script's text.
# The installed version: the launcher's own word, nothing where it does not answer.
VERSION = 'exec "$HOME/.local/bin/claude" --version'
# The download: $1 the release, $2 the platform, $3 the binary's URL. Its SHA-256 said back.
DOWNLOAD = f"""set -eu
umask 077
mkdir -p "$HOME/.claude/downloads"
chmod 700 "$HOME/.claude/downloads"
dest="$HOME/.claude/downloads/claude-$1-$2"
rm -f -- "$dest"
curl --fail --silent --show-error --location --proto =https --proto-redir =https --max-time {DOWNLOAD_SECONDS} \\
  --output "$dest" "$3"
sha256sum -- "$dest"
"""
# The install: $1 the release, $2 the platform, $3 the SHA-256 the signed manifest gives. The download run only where
# it is that binary; then the SHA-256 of what the launcher resolves to said back.
INSTALL = f"""set -eu
dest="$HOME/.claude/downloads/claude-$1-$2"
got=$(sha256sum -- "$dest")
if [ "${{got%% *}}" != "$3" ]; then
  echo "the download is not the binary the signed manifest names: it was not run" >&2
  exit 3
fi
chmod 700 "$dest"
timeout {INSTALL_SECONDS} "$dest" install "$1" >&2
sha256sum -- "$(readlink -f -- "$HOME/{LAUNCHER}")"
"""
# The download removed: $1 the release, $2 the platform.
REMOVE = 'rm -f -- "$HOME/.claude/downloads/claude-$1-$2"'

# A SHA-256 as sha256sum says one, at the start of its line.
DIGEST = re.compile(r"([0-9a-f]{64}) [ *].*")


@dataclasses.dataclass(frozen=True)
class Installed:
    """What the probe found: the method and the release the settings name (a channel or a version), and the version
    installed: None where none is, "" where one is that says no version."""

    method: str
    wanted: str
    version: str | None


def _words(said: str) -> str:
    return text.one_line(said, 300)


def _failure(result: runner.Result) -> str:
    """Why a call did not end well, in a few words."""
    if isinstance(result, runner.Done):
        said = _words(result.said)
        return f"exit {result.status}" + (f": {said}" if said else "")
    if isinstance(result, runner.Unreachable):
        return "the machine was not reached (ssh's exit 255)" + (f": {_words(result.said)}" if result.said else "")
    if isinstance(result, runner.TimedOut):
        return "it took longer than its time"
    if isinstance(result, runner.Silent):
        return "it said nothing in its time"
    assert isinstance(result, runner.Cut)
    return _words(result.why)


def _sh(script: str, *words: str) -> list[str]:
    return ["sh", "-c", script, "sh", *words]


def _digest(result: runner.Result) -> str | None:
    """The SHA-256 the last line of a call that ended well said, or None."""
    if not isinstance(result, runner.Done) or result.status != 0 or not result.lines:
        return None
    matched = DIGEST.fullmatch(result.lines[-1].strip())
    return matched.group(1) if matched else None


def _shown(value: object) -> str:
    return json.dumps(text.one_line(str(value), 100))


class ClaudeInstall:
    """The step: see the module's docstring. key, the key Anthropic's manifest is verified by: the vendored one."""

    name = "claude-install"
    part = "claude_code"

    def __init__(self, *, key: keys.Key = keys.CLAUDE_CODE) -> None:
        if not isinstance(key, keys.Key):
            raise TypeError("Claude Code's release is verified by a vendored key, a keys.Key")
        self.key = key

    # ------------------------------------------------------------ the settings ----

    @staticmethod
    def _setting(ctx: Context, name: str) -> str:
        if name in OPTIONS:
            value = ctx.settings.get(name, OPTIONS[name])
        elif name in ctx.settings:
            value = ctx.settings[name]
        else:
            raise Failed(f"the run's settings hold no {name}, which Claude Code's install reads")
        value = "" if value is None else value
        if not isinstance(value, str):
            raise Failed(f"{name} is text, not {_shown(value)}")
        return value

    def _wanted(self, ctx: Context) -> tuple[str, str]:
        """The method and the release the settings name, each held before anything runs."""
        method = self._setting(ctx, "claude_code_install_method")
        if method not in METHODS:
            raise Failed(f"claude_code_install_method is {_shown(method)}: it is native or npm")
        wanted = self._setting(ctx, "claude_code_version")
        if wanted not in CHANNELS and not RELEASE.fullmatch(wanted):
            raise Failed(
                f"claude_code_version is {_shown(wanted)}: it is stable, latest or a version such as 2.1.311"
                + (", and npm would install anything else given as a version as root" if method == "npm" else "")
            )
        return method, wanted

    # ------------------------------------------------------------ the probe ----

    async def probe(self, ctx: Context) -> Installed:
        """What is installed, by the method the settings name: nothing changed."""
        method, wanted = self._wanted(ctx)
        if method == "npm":
            return Installed(method, wanted, await self._npm_listed(ctx))
        result = await ctx.run(_sh(VERSION), limits=QUICK)
        if isinstance(result, runner.Done):
            if result.status != 0:
                return Installed(method, wanted, None)
            first = result.lines[0].split() if result.lines and result.lines[0].split() else [""]
            return Installed(method, wanted, first[0] if RELEASE.fullmatch(first[0]) else "")
        raise Failed(f"Claude Code's version could not be read on the machine: {_failure(result)}")

    async def _npm_listed(self, ctx: Context) -> str | None:
        """The version of npm's global package, as root: None where npm lists none. npm must answer first."""
        answered = await ctx.run(["npm", "--version"], as_root=True, limits=QUICK)
        if not (isinstance(answered, runner.Done) and answered.status == 0):
            raise Failed(
                "npm did not answer on the machine, and claude_code_install_method npm installs Claude Code with it"
                f" (dev_tools_install_node installs Node.js and npm): {_failure(answered)}"
            )
        listed = await ctx.run(["npm", "ls", "-g", "--depth=0", "--json"], as_root=True, limits=QUICK)
        # npm ls exits 1 for a problem of the tree it lists, and still lists it.
        if not (isinstance(listed, runner.Done) and listed.status in (0, 1)):
            raise Failed(f"npm did not list its global packages on the machine: {_failure(listed)}")
        try:
            tree = json.loads("\n".join(listed.lines))
        except ValueError:
            tree = None
        if not isinstance(tree, dict):
            raise Failed("npm listed its global packages in no JSON object")
        dependencies = tree.get("dependencies") or {}
        package = dependencies.get(PACKAGE) if isinstance(dependencies, dict) else None
        if package is None:
            return None
        version = package.get("version") if isinstance(package, dict) else None
        return version if isinstance(version, str) and RELEASE.fullmatch(version) else ""

    # ------------------------------------------------------------ the difference ----

    def difference(self, state: object) -> Difference | None:
        """The release installed and the one wanted, where apply would install one; None where it would not."""
        if not isinstance(state, Installed):
            raise TypeError("claude-install's difference is of its own probe's state, an Installed")
        wanted = state.wanted if state.wanted not in CHANNELS else f"the {state.wanted} channel's release"
        if state.version is None:
            return Difference(f"Claude Code ({state.method})", "not installed", wanted)
        if state.wanted not in CHANNELS and state.version != state.wanted:
            return Difference(f"Claude Code ({state.method})", state.version or "a version it does not say", wanted)
        return None

    # ------------------------------------------------------------ the apply ----

    async def apply(self, ctx: Context, state: object) -> bool:
        """The release wanted installed where the difference names one: whether anything was."""
        if self.difference(state) is None:
            return False
        assert isinstance(state, Installed)
        if state.method == "npm":
            await self._npm(ctx, state)
        else:
            await self._native(ctx, state)
        return True

    async def _release(self, ctx: Context, wanted: str) -> downloads.Release:
        """The release wanted names, its manifest's signature verified on this computer."""
        import asyncio

        base = self._setting(ctx, "claude_code_download_url")
        tls_ca_file = self._setting(ctx, "tls_ca_file") or None
        try:
            chosen = proxy.choose(self._setting(ctx, "network_proxy"), ctx.env, base)
            return await asyncio.to_thread(
                downloads.claude_release, base, wanted, key=self.key, tls_ca_file=tls_ca_file, proxy=chosen
            )
        except (downloads.DownloadError, trust.Unusable, ValueError, TypeError) as error:
            raise Failed(
                f"Claude Code was not installed, nothing fetched on the machine: {_words(str(error))}"
            ) from None

    async def _platform(self, ctx: Context) -> str:
        result = await ctx.run(["uname", "-m"], limits=QUICK)
        answered = isinstance(result, runner.Done) and result.status == 0 and result.lines
        machine = result.lines[0].strip() if answered else ""
        if not machine:
            raise Failed(f"the machine's platform could not be read (uname -m): {_failure(result)}")
        return PLATFORMS.get(machine, machine)

    async def _native(self, ctx: Context, state: Installed) -> None:
        release = await self._release(ctx, state.wanted)
        platform = await self._platform(ctx)
        digest = release.checksums.get(platform)
        if digest is None:
            raise Failed(
                f"the signed manifest of Claude Code {release.version} has no binary for the machine's platform,"
                f" {_shown(platform)}: nothing was fetched on the machine"
            )
        base = self._setting(ctx, "claude_code_download_url").rstrip("/")
        url = f"{base}/{release.version}/{platform}/claude"
        try:
            fetched = await ctx.run(_sh(DOWNLOAD, release.version, platform, url), limits=SLOW)
            got = _digest(fetched)
            if got is None:
                raise Failed(
                    f"Claude Code {release.version}'s binary was not downloaded on the machine: {_failure(fetched)}"
                )
            if got != digest:
                raise Failed(
                    f"the binary downloaded for Claude Code {release.version} ({platform}) is not the one Anthropic's"
                    f" signed manifest names (SHA-256 {digest}): it was never run"
                )
            installed = await ctx.run(_sh(INSTALL, release.version, platform, digest), limits=SLOW)
            if not (isinstance(installed, runner.Done) and installed.status == 0):
                raise Failed(f"Claude Code {release.version}'s install did not end well: {_failure(installed)}")
            if _digest(installed) != digest:
                raise Failed(
                    f"~/{LAUNCHER} is not the binary Anthropic's signed manifest of {release.version} gives"
                    f" ({platform}, SHA-256 {digest}): install put something else in place"
                )
        finally:
            await ctx.run(_sh(REMOVE, release.version, platform), limits=QUICK)

    async def _npm(self, ctx: Context, state: Installed) -> None:
        exact = state.wanted not in CHANNELS
        spec = f"{PACKAGE}@{state.wanted}" if exact else PACKAGE
        asked = ["timeout", str(NPM_SECONDS), "npm", "install", "-g", spec]
        result = await ctx.run(asked, as_root=True, limits=SLOW)
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(f"npm did not install {spec} on the machine: {_failure(result)}")
        version = await self._npm_listed(ctx)
        if version is None or (exact and version != state.wanted):
            raise Failed(f"npm installed {spec}, and lists {_shown(version or 'none')} after it")
