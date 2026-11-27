"""Layer 2's one shape for a step, its runner, and the step that installs Claude Code (lib/claude_vm/guest/layer2/;
docs/architecture/engine.md §16.10).

Every machine is F30's stand-in (tests/unit/aw_machine.py), reached through B3's runner and the stand-in ssh; a release
is served by the fake download server (tests/molecule/fake_downloads.py) over TLS, with a node's certificate
tests/unit/pki.py made, whose CA stands in for certifi's roots here and is the machine's curl's (a stand-in in its
bin/ hands curl the CA's file, and nothing else), and signed by keys tests/unit/signing.py makes, never Anthropic's.
The binary a release serves is a stand-in for Claude Code's that says its version and installs itself as the real
one's `install` does, as tests/unit/test_signed_downloads.py's does; test_signed_downloads.py is unchanged and still
holds today's role.

- Its cases, carried against the step: the release a channel names, or a version, installed as that release, its
  binary the signed manifest's and what the launcher resolves to the same, the download gone; the installer script
  never fetched and the signature always; a manifest that does not verify (changed after it was signed, or signed by
  another key) stops the install before any binary is fetched; a manifest signed for another release, or one past its
  cap, refused; a binary that is not the manifest's never run; an install that puts another binary in place refused; a
  release older than the first signed one, or a channel that names no version, refused, nothing of it fetched.
- A launcher the step did not verify (linked outside ~/.local/share/claude/versions/, linked to a file there that is
  not the signed binary, or a file of its own) is installed over, nothing of it run; an install that leaves one is
  refused on every run, the launcher removed; a download changed on the machine between its download and its install
  is never run.
- A second run of either method applies nothing; an exact version moves it; the difference names the release
  installed and the one wanted; a platform the release has no binary for is refused; and guest.states reports the
  version installed, natively and by npm.
- npm, against a stand-in npm on the machine, as root through its sudo: installed once, a second run applying
  nothing, an exact version moving it, stable and latest never given to npm as a version; npm not answering, a sudo
  that wants a password and a version npm would read as another spec each a named failure, nothing installed.
- The steps' runner: a machine's steps in order, each between two step events, the first that fails stopping the
  machine, a run that is stopping starting no next step, and steps held before anything runs.
- The settings: the two options the step reads are claude_code's passed through, with the role's own defaults.
- The invariant with a test of its own: the step's source gives `install` the release verified, never a channel's
  name, and names no URL of the installer script.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import shlex
import shutil
import sys
import threading
from pathlib import Path

import pytest
import yaml
from aw_machine import SYSTEM_PATH, Machine, filed, machine, ssh_stand_in, target
from harness import REPO, _executable
from pki import proxmox_pki
from signing import Signer
from test_aw_downloads import _certifi, _served
from test_aw_guest_remote import _settings as _status_settings

from claude_vm.fleet import passthrough, paths, registry
from claude_vm.guest import states
from claude_vm.guest.layer2 import claude_install, step
from claude_vm.guest.layer2.claude_install import ClaudeInstall
from claude_vm.runs import job as jobs
from claude_vm.vendors import downloads, keys

# The certificate authority of the fake and the vendor's stand-in key: cheap enough to make again on each worker
# (tests/unit/conftest.py).
EACH_WORKER = {"pki", "signer"}
SOURCE = REPO / "lib" / "claude_vm" / "guest" / "layer2" / "claude_install.py"
DEFAULTS = REPO / "roles" / "claude_code" / "defaults" / "main.yml"
RELEASE = "2.1.400"
NEXT = "2.1.401"
OLD = "2.1.50"
PLATFORMS = ("linux-x64", "linux-arm64")
STEP = "claude-install"


def _binary(version: str = RELEASE) -> bytes:
    """The stand-in for Claude Code's binary: says its version, or says what it was asked and installs itself as the
    real one's `install` does, into ~/.local/share/claude/versions/, linked from ~/.local/bin
    (test_signed_downloads.py's BINARY)."""
    return f"""#!/bin/sh
[ "$1" = --version ] && {{ echo "{version} (Claude Code)"; exit 0; }}
printf '%s\\n' "$*" >> "$HOME/asked"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/claude/versions"
cp "$0" "$HOME/.local/share/claude/versions/$2"
ln -sf "$HOME/.local/share/claude/versions/$2" "$HOME/.local/bin/claude"
""".encode()


# One that fetches another binary for itself and installs that: what the signed manifest held is not what is run.
REFETCHING = f"""#!/bin/sh
[ "$1" = --version ] && {{ echo "{RELEASE} (Claude Code)"; exit 0; }}
printf '%s\\n' "$*" >> "$HOME/asked"
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\\necho fetched again\\n' > "$HOME/.local/bin/claude"
chmod +x "$HOME/.local/bin/claude"
""".encode()

# The stand-in npm: `--version`; `ls -g --depth=0 --json` of the packages it holds; `install -g <spec>`, the spec's
# version or LATEST for none. Its state and each call's words, with whether sudo ran it, beside the machine.
NPM = f"""#!{sys.executable} -I
import json, os, sys
here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
state_file, args = os.path.join(here, "npm.json"), sys.argv[1:]
with open(os.path.join(here, "npm.log"), "a") as log:
    log.write(json.dumps({{"args": args, "sudo": "SUDO_USER" in os.environ}}) + "\\n")
state = json.load(open(state_file)) if os.path.exists(state_file) else {{}}
if args == ["--version"]:
    print("10.9.2")
elif args[:1] == ["ls"] and "-g" in args and "--json" in args:
    listed = {{name: {{"version": version}} for name, version in state.items()}}
    print(json.dumps({{"name": "lib", "dependencies": listed}}, indent=2))
elif args[:1] == ["install"] and "-g" in args:
    spec = [word for word in args[1:] if not word.startswith("-")][0]
    name, at, version = spec[1:].partition("@")
    state["@" + name] = version if at else "{RELEASE}"
    json.dump(state, open(state_file, "w"))
else:
    sys.stderr.write("npm stand-in: not a call it takes\\n")
    sys.exit(1)
"""
# An npm that does not answer, as a machine with no Node.js has none.
NO_NPM = "#!/bin/sh\necho 'sh: 1: npm: not found' >&2\nexit 127\n"
# One that installs itself as the real one would, but under a folder of its own, and links the launcher there: the
# manifest's binary, in a place the step does not hold.
LINKING = f"""#!/bin/sh
[ "$1" = --version ] && {{ echo "{RELEASE} (Claude Code)"; exit 0; }}
printf '%s\\n' "$*" >> "$HOME/asked"
mkdir -p "$HOME/.local/bin" "$HOME/elsewhere"
cp "$0" "$HOME/elsewhere/claude"
ln -sf "$HOME/elsewhere/claude" "$HOME/.local/bin/claude"
""".encode()
# What a test writes over the download on the machine, between the download and the install.
SWAPPED = b'#!/bin/sh\necho swapped-on-machine >> "$HOME/asked"\n'


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(scope="module")
def signer() -> Signer:
    """Anthropic's key, as the test stands in for it."""
    return Signer()


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def _manifest(version: str = RELEASE, binary: bytes | None = None) -> bytes:
    digest = hashlib.sha256(_binary(version) if binary is None else binary).hexdigest()
    platforms = {name: {"binary": "claude", "checksum": digest, "size": 1} for name in PLATFORMS}
    return json.dumps({"version": version, "platforms": platforms}, indent=2).encode()


class Vendor:
    """The fake download server over TLS, serving root's files, each folder of root a way a release can be served;
    and the key file of the test's signer."""

    def __init__(self, root: Path, base: str, key: keys.Key, signer: Signer) -> None:
        self.root, self.base, self.key, self.signer = root, base, key, signer

    def release(
        self,
        folder: str = "good",
        *,
        version: str = RELEASE,
        manifest: bytes | None = None,
        binary: bytes | None = None,
        signed: bytes | None = None,
        signer: Signer | None = None,
    ) -> None:
        """A release under folder as Anthropic lays one out: the channels, the manifest and its signature, the
        binaries."""
        base = self.root / folder
        _write(base / "stable", version + "\n")
        _write(base / "latest", version + "\n")
        body = _manifest(version) if manifest is None else manifest
        _write(base / version / "manifest.json", body)
        _write(base / version / "manifest.json.sig", (signer or self.signer).sign(body if signed is None else signed))
        for platform in PLATFORMS:
            _write(base / version / platform / "claude", _binary(version) if binary is None else binary)

    def url(self, folder: str = "good") -> str:
        return f"{self.base}/{folder}"

    def asked(self) -> list[str]:
        """Every path asked of the fake, in order."""
        log = self.root / "requests"
        return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def vendor(tmp_path, pki, signer, monkeypatch):
    _certifi(monkeypatch, tmp_path, pki["ca"])
    key = tmp_path / "vendor.asc"
    key.write_text(signer.public())
    server, base = _served(tmp_path / "served", pki)
    try:
        yield Vendor(tmp_path / "served", base, keys.Key(key, signer.fingerprint), signer)
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture
def job(home: Path):
    """A job of this process's own, as a run that starts the runner's children has; dropped after the test."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", None, ["alpha"], {"kind": "terminal"}, True
    )
    yield made
    jobs.drop(made)


def _stand_in(made: Machine, name: str, text: str) -> None:
    _executable(made.bin / name, text)


@pytest.fixture
def alpha(tmp_path, engine_guard, pki) -> Machine:
    """A stand-in machine alpha with no Claude Code, its key filed, the stand-in ssh first on this test's PATH; its curl
    the computer's, handed the fake's CA; its platform x86_64; and, where the computer has none (macOS), the GNU tools
    the step runs there, sha256sum and timeout, as stand-ins."""
    root = tmp_path / "machines"
    made = machine(root, "alpha")
    filed(made)
    engine_guard.first_on_path(ssh_stand_in(root, [made]))
    system = os.pathsep.join(SYSTEM_PATH)
    curl = shutil.which("curl", path=system)
    assert curl, "the step downloads with the machine's curl, which this computer's system folders must have"
    ca = tmp_path / "ca.pem"
    ca.write_text(pki["ca"])
    _stand_in(made, "curl", f'#!/bin/sh\nexec {shlex.quote(curl)} --cacert {shlex.quote(str(ca))} "$@"\n')
    _stand_in(made, "uname", '#!/bin/sh\n[ "$1" = -m ] && { echo x86_64; exit 0; }\nexec /usr/bin/uname "$@"\n')
    if not shutil.which("sha256sum", path=system):
        _stand_in(
            made,
            "sha256sum",
            f'#!{sys.executable} -I\nimport hashlib, sys\npath = sys.argv[-1]\nprint(hashlib.sha256(open(path, "rb")'
            '.read()).hexdigest() + "  " + path)\n',
        )
    if not shutil.which("timeout", path=system):
        _stand_in(made, "timeout", '#!/bin/sh\nshift\nexec "$@"\n')
    return made


def _settings(vendor: Vendor, folder: str = "good", **over: object) -> dict[str, object]:
    return {
        "claude_code_version": "stable",
        "claude_code_download_url": vendor.url(folder),
        "tls_ca_file": "",
        "network_proxy": "none",
        **over,
    }


def _ctx(job: jobs.Job, made: Machine, settings, **over) -> step.Context:
    given = {"cancelled": threading.Event(), "become_exe": "sudo", **over}
    return step.Context(job, target(made), made.identity, settings, dict(os.environ), **given)


def _install(job: jobs.Job, made: Machine, vendor: Vendor, **settings: object) -> step.Ran:
    """The step run on made, its release verified by the test's key, its settings vendor's good release's but those
    given."""
    folder = str(settings.pop("folder", "good"))
    ctx = _ctx(job, made, _settings(vendor, folder, **settings))
    return asyncio.run(step.run_steps(ctx, [ClaudeInstall(key=vendor.key)]))


def _asked(made: Machine) -> list[str]:
    """What the release's binary was asked, each time it ran but for its version."""
    asked = made.home / "asked"
    return asked.read_text().splitlines() if asked.exists() else []


def _downloads(made: Machine) -> list[str]:
    folder = made.home / ".claude" / "downloads"
    return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


def _launched(made: Machine) -> str:
    """The SHA-256 of what the launcher resolves to."""
    return hashlib.sha256((made.home / ".local" / "bin" / "claude").resolve().read_bytes()).hexdigest()


def _npm_calls(made: Machine) -> list[dict]:
    log = made.directory / "npm.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# ---------------------------------------------------------------------------------------------- native ---
@pytest.mark.parametrize("wanted", ["stable", "latest", RELEASE])
def test_the_release_named_is_installed_as_that_release_held_to_the_signed_manifest(job, alpha, vendor, wanted):
    vendor.release()
    ran = _install(job, alpha, vendor, claude_code_version=wanted)
    assert ran == step.Ran("succeeded", ((STEP, True),)), ran
    # The release the channel named, never the channel's name, which the binary could resolve and fetch again.
    assert _asked(alpha) == [f"install {RELEASE}"]
    assert _launched(alpha) == hashlib.sha256(_binary()).hexdigest()
    # The download is gone, from the account's own folder, never /tmp, which may be mounted noexec.
    assert _downloads(alpha) == []
    asked = vendor.asked()
    assert not any(path.endswith("install.sh") for path in asked)
    assert f"/good/{RELEASE}/manifest.json.sig" in asked and f"/good/{RELEASE}/linux-x64/claude" in asked
    # A channel's pointer is read only for a channel, once; an exact version reads none.
    assert [path for path in asked if path in ("/good/stable", "/good/latest")] == (
        [] if wanted == RELEASE else [f"/good/{wanted}"]
    )
    # A second run applies nothing and runs nothing of the binary: it holds what is installed to the signed manifest of
    # its own release, and fetches no pointer and no binary.
    again = _install(job, alpha, vendor, claude_code_version=wanted)
    assert again == step.Ran("succeeded", ((STEP, False),)) and _asked(alpha) == [f"install {RELEASE}"]
    assert vendor.asked()[len(asked) :] == [f"/good/{RELEASE}/manifest.json", f"/good/{RELEASE}/manifest.json.sig"]


def test_an_exact_version_moves_it_and_a_second_run_applies_nothing(job, alpha, vendor):
    vendor.release()
    vendor.release(version=NEXT)
    _write(vendor.root / "good" / "stable", RELEASE + "\n")
    assert _install(job, alpha, vendor).outcome == "succeeded"
    moved = _install(job, alpha, vendor, claude_code_version=NEXT)
    assert moved == step.Ran("succeeded", ((STEP, True),)), moved
    assert _asked(alpha) == [f"install {RELEASE}", f"install {NEXT}"]
    assert _launched(alpha) == hashlib.sha256(_binary(NEXT)).hexdigest()
    assert _install(job, alpha, vendor, claude_code_version=NEXT) == step.Ran("succeeded", ((STEP, False),))
    # A channel keeps what is installed: the binary updates itself on its channel.
    assert _install(job, alpha, vendor) == step.Ran("succeeded", ((STEP, False),))
    assert _asked(alpha) == [f"install {RELEASE}", f"install {NEXT}"]


@pytest.mark.parametrize(
    ("how", "why"),
    [
        ({"signed": _manifest(RELEASE, b"another binary")}, "the data is not what was signed"),
        ({"stranger": True}, "a signature by another key than"),
    ],
)
def test_a_manifest_that_does_not_verify_stops_the_install_before_any_binary_is_fetched(job, alpha, vendor, how, why):
    if how.pop("stranger", False):
        how["signer"] = Signer()
    vendor.release(**how)
    ran = _install(job, alpha, vendor)
    assert ran.outcome == "failed" and why in ran.why and "nothing fetched on the machine" in ran.why, ran
    assert not any("/linux-" in path for path in vendor.asked())
    assert _asked(alpha) == [] and _downloads(alpha) == [] and not (alpha.home / ".claude" / "downloads").exists()


def test_a_signed_manifest_of_another_release_is_refused(job, alpha, vendor):
    # Signed, but of another release than the channel named: served in its place, it would install that one.
    vendor.release(manifest=_manifest("2.1.399"))
    ran = _install(job, alpha, vendor)
    assert ran.outcome == "failed" and "names version" in ran.why and "2.1.399" in ran.why, ran
    assert _asked(alpha) == [] and not any("/linux-" in path for path in vendor.asked())


def test_a_manifest_past_its_cap_is_refused_and_its_signature_never_asked_for(job, alpha, vendor):
    vendor.release(manifest=b" " * (downloads.DOCUMENT_MOST + 1))
    ran = _install(job, alpha, vendor)
    assert ran.outcome == "failed" and "nothing fetched on the machine" in ran.why, ran
    assert f"/good/{RELEASE}/manifest.json" in vendor.asked()
    assert f"/good/{RELEASE}/manifest.json.sig" not in vendor.asked() and _asked(alpha) == []


def test_a_binary_that_is_not_the_one_the_signed_manifest_names_is_never_run_and_its_download_goes(job, alpha, vendor):
    vendor.release(binary=b'#!/bin/sh\necho swapped >> "$HOME/asked"\n', manifest=_manifest())
    ran = _install(job, alpha, vendor)
    assert ran.outcome == "failed" and "it was never run" in ran.why, ran
    assert _asked(alpha) == [] and not (alpha.home / ".local").exists()
    assert _downloads(alpha) == []


@pytest.mark.parametrize("binary", [REFETCHING, LINKING], ids=["another-binary", "linked-elsewhere"])
@pytest.mark.parametrize("wanted", ["stable", RELEASE])
def test_an_install_that_puts_something_else_in_place_is_refused_on_every_run(job, alpha, vendor, binary, wanted):
    vendor.release(binary=binary, manifest=_manifest(binary=binary))
    launcher = alpha.home / ".local" / "bin" / "claude"
    for runs in (1, 2):
        ran = _install(job, alpha, vendor, claude_code_version=wanted)
        assert ran.outcome == "failed" and "install put something else in place" in ran.why, ran
        # The launcher it left is removed, so that no run after takes it as installed.
        assert not os.path.lexists(launcher) and _downloads(alpha) == []
        assert _asked(alpha) == [f"install {RELEASE}"] * runs


def _placed(made: Machine, how: str) -> None:
    """A launcher the step did not verify: linked to the right binary outside the releases' folder, linked to a file
    of the releases' folder that is not the signed binary, or a file of its own."""
    home = made.home
    launcher = home / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    if how == "elsewhere":
        _write(home / "elsewhere" / "claude", _binary())
        launcher.symlink_to(home / "elsewhere" / "claude")
    elif how == "tampered":
        _write(home / ".local" / "share" / "claude" / "versions" / RELEASE, _binary() + b"# changed\n")
        launcher.symlink_to(home / ".local" / "share" / "claude" / "versions" / RELEASE)
    else:
        _write(launcher, _binary())
    launcher.resolve().chmod(0o755)


@pytest.mark.parametrize(
    ("how", "said"),
    [
        ("elsewhere", "no release's file under ~/.local/share/claude/versions"),
        ("tampered", f"it is not the binary Anthropic's signed manifest of {RELEASE} gives (linux-x64)"),
        ("a-file", "no release's file under ~/.local/share/claude/versions"),
    ],
)
@pytest.mark.parametrize("wanted", ["stable", RELEASE])
def test_a_launcher_the_step_did_not_verify_is_installed_over(job, alpha, vendor, how, said, wanted):
    vendor.release()
    _placed(alpha, how)
    install = ClaudeInstall(key=vendor.key)
    ctx = _ctx(job, alpha, _settings(vendor, claude_code_version=wanted))
    difference = install.difference(asyncio.run(install.probe(ctx)))
    assert difference is not None and said in difference.now, difference
    # The probe ran nothing of what the launcher resolves to.
    assert _asked(alpha) == []
    assert _install(job, alpha, vendor, claude_code_version=wanted) == step.Ran("succeeded", ((STEP, True),))
    assert _asked(alpha) == [f"install {RELEASE}"] and _launched(alpha) == hashlib.sha256(_binary()).hexdigest()
    versions = alpha.home / ".local" / "share" / "claude" / "versions"
    assert (alpha.home / ".local" / "bin" / "claude").resolve() == (versions / RELEASE).resolve()
    assert _install(job, alpha, vendor, claude_code_version=wanted) == step.Ran("succeeded", ((STEP, False),))


def test_a_download_changed_on_the_machine_before_its_install_is_never_run(job, alpha, vendor, monkeypatch):
    vendor.release()
    run = step.Context.run

    async def swapping(self, command, **how):
        """The context's run, the download written over on the machine once its call has said its digest."""
        result = await run(self, command, **how)
        if list(command[:3]) == ["sh", "-c", claude_install.DOWNLOAD]:
            (alpha.home / ".claude" / "downloads" / f"claude-{RELEASE}-linux-x64").write_bytes(SWAPPED)
        return result

    monkeypatch.setattr(step.Context, "run", swapping)
    ran = _install(job, alpha, vendor)
    assert ran.outcome == "failed" and "exit 3: the download is not the binary" in ran.why, ran
    assert _asked(alpha) == [] and _downloads(alpha) == [] and not (alpha.home / ".local").exists()


@pytest.mark.parametrize(
    ("folder", "wanted", "pointer"),
    [("old", "stable", f"{OLD}\n"), ("html", "stable", "<html>Service unavailable</html>\n"), ("good", OLD, None)],
)
def test_a_release_older_than_the_first_signed_one_or_none_is_refused(job, alpha, vendor, folder, wanted, pointer):
    vendor.release(folder, version=OLD)
    if pointer is not None:
        _write(vendor.root / folder / "stable", pointer)
    ran = _install(job, alpha, vendor, folder=folder, claude_code_version=wanted)
    assert ran.outcome == "failed" and downloads.SIGNED_SINCE in ran.why, ran
    assert not any(f"/{OLD}/" in path for path in vendor.asked()) and _asked(alpha) == []


def test_a_platform_the_release_has_no_binary_for_is_refused_and_nothing_fetched(job, alpha, vendor):
    vendor.release()
    _stand_in(alpha, "uname", "#!/bin/sh\necho riscv64\n")
    ran = _install(job, alpha, vendor)
    assert ran.outcome == "failed" and '"riscv64"' in ran.why and "no binary for" in ran.why, ran
    assert not any("/linux-" in path for path in vendor.asked()) and _asked(alpha) == []


def test_the_difference_names_the_release_installed_and_the_one_wanted(job, alpha, vendor):
    vendor.release()
    _stand_in(alpha, "npm", NPM)
    install = ClaudeInstall(key=vendor.key)

    def difference(**settings: object) -> step.Difference | None:
        ctx = _ctx(job, alpha, _settings(vendor, **settings))
        return install.difference(asyncio.run(install.probe(ctx)))

    assert difference() == step.Difference("Claude Code (native)", "not installed", "the stable channel's release")
    # A probe and a difference change nothing and fetch nothing.
    assert vendor.asked() == [] and _asked(alpha) == []
    assert _install(job, alpha, vendor).outcome == "succeeded"
    assert difference() is None and difference(claude_code_version=RELEASE) is None
    assert difference(claude_code_version=NEXT) == step.Difference("Claude Code (native)", RELEASE, NEXT)
    assert difference(claude_code_install_method="npm") == step.Difference(
        "Claude Code (npm)", "not installed", "the stable channel's release"
    )


def test_guest_states_reports_an_npm_install_and_its_version(job, alpha, vendor):
    given = _status_settings(alpha)
    settings = {**given, "claude": {**given["claude"], "method": "npm"}}

    def claude() -> tuple[bool, str]:
        records = asyncio.run(
            states.read(
                job, target(alpha), ["claude"], settings=settings, name="alpha", python="python3", env=dict(os.environ)
            )
        )
        return records["claude"]["installed"], records["claude"]["version"]

    _stand_in(alpha, "npm", NO_NPM)
    assert claude() == (False, "")
    _stand_in(alpha, "npm", NPM)
    assert claude() == (False, "")
    npm_next = {"claude_code_install_method": "npm", "claude_code_version": NEXT}
    assert _install(job, alpha, vendor, **npm_next).outcome == "succeeded"
    assert claude() == (True, NEXT)
    # Read as the machine's user: status asks nothing of sudo for it.
    assert [call["sudo"] for call in _npm_calls(alpha) if call["args"][:1] == ["ls"]][-1] is False


def test_guest_states_reports_the_version_installed(job, alpha, vendor):
    vendor.release()
    assert _install(job, alpha, vendor).outcome == "succeeded"
    records = asyncio.run(
        states.read(
            job,
            target(alpha),
            ["claude"],
            settings=_status_settings(alpha),
            name="alpha",
            python="python3",
            env=dict(os.environ),
        )
    )
    assert records["claude"]["installed"] is True and records["claude"]["version"] == RELEASE


def test_an_install_said_as_the_machine_s_step_events(job, alpha, vendor):
    vendor.release()
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    ctx = _ctx(job, alpha, _settings(vendor), stage="configure")
    asyncio.run(step.run_steps(ctx, [ClaudeInstall(key=vendor.key)]))
    said = [(event.kind, event.machine, event.text, dict(event.data)) for event in heard]
    staged = {"stage": "configure"}
    assert said == [
        ("step", alpha.identity, STEP, {"part": "claude_code", **staged}),
        ("step", alpha.identity, STEP, {"part": "claude_code", "outcome": "done", "changed": True, **staged}),
    ]


# ---------------------------------------------------------------------------------------------- npm ---
@pytest.mark.parametrize("channel", ["stable", "latest"])
def test_npm_installs_once_and_an_exact_version_moves_it_never_given_a_channel(job, alpha, vendor, channel):
    _stand_in(alpha, "npm", NPM)
    method = {"claude_code_install_method": "npm"}
    assert _install(job, alpha, vendor, claude_code_version=channel, **method) == step.Ran("succeeded", ((STEP, True),))
    assert _install(job, alpha, vendor, claude_code_version=channel, **method) == step.Ran(
        "succeeded", ((STEP, False),)
    )
    assert _install(job, alpha, vendor, claude_code_version=NEXT, **method) == step.Ran("succeeded", ((STEP, True),))
    assert _install(job, alpha, vendor, claude_code_version=NEXT, **method) == step.Ran("succeeded", ((STEP, False),))
    calls = _npm_calls(alpha)
    installs = [call["args"] for call in calls if call["args"][:1] == ["install"]]
    package = claude_install.PACKAGE
    assert installs == [["install", "-g", package], ["install", "-g", f"{package}@{NEXT}"]]
    assert not any(channel in word for call in calls for word in call["args"])
    # As root, through the machine's sudo, which is never asked for a password; and nothing of Anthropic's fetched.
    assert all(call["sudo"] for call in calls) and all(each["n"] and not each["S"] for each in alpha.sudo_calls())
    assert vendor.asked() == [] and json.loads((alpha.directory / "npm.json").read_text()) == {
        claude_install.PACKAGE: NEXT
    }


def test_npm_not_answering_is_a_named_failure_and_nothing_is_installed(job, alpha, vendor):
    _stand_in(alpha, "npm", NO_NPM)
    ran = _install(job, alpha, vendor, claude_code_install_method="npm")
    assert ran.outcome == "failed" and ran.why.startswith("npm did not answer on the machine"), ran
    assert "dev_tools_install_node" in ran.why and "exit 127" in ran.why


def test_a_sudo_that_wants_a_password_is_a_named_failure_and_is_sent_none(tmp_path, engine_guard, job, vendor):
    root = tmp_path / "asking"
    made = machine(root, "alpha", sudo_password="the machine's own")
    filed(made)
    engine_guard.first_on_path(ssh_stand_in(root, [made]))
    _stand_in(made, "npm", NPM)
    ran = _install(job, made, vendor, claude_code_install_method="npm")
    assert ran.outcome == "failed" and "asks dev for a password, and a step sends none" in ran.why, ran
    assert [each["n"] for each in made.sudo_calls()] == [True] and _npm_calls(made) == []


@pytest.mark.parametrize("wanted", ["github:someone/claude-code", "2.1", "^2.1.400", "2.1.400 || 9", "", "stable\n"])
@pytest.mark.parametrize("method", ["npm", "native"])
def test_a_version_npm_would_read_as_another_spec_is_refused_before_anything_runs(job, alpha, vendor, wanted, method):
    _stand_in(alpha, "npm", NPM)
    ran = _install(job, alpha, vendor, claude_code_version=wanted, claude_code_install_method=method)
    assert ran.outcome == "failed" and ran.why.startswith("claude_code_version is "), ran
    assert ("npm would install anything else" in ran.why) is (method == "npm")
    assert alpha.calls() == [] and _npm_calls(alpha) == [] and vendor.asked() == []


def test_a_method_that_is_neither_is_refused_before_anything_runs(job, alpha, vendor):
    ran = _install(job, alpha, vendor, claude_code_install_method="package")
    assert ran.outcome == "failed" and ran.why == 'claude_code_install_method is "package": it is native or npm'
    assert alpha.calls() == []


# ---------------------------------------------------------------------------------------------- the runner ---
class _Step:
    """A step of the test's own: what it was asked, in order, in seen; raises Failed from its apply where told."""

    def __init__(self, name: str, seen: list[str], fails: bool = False, then=None) -> None:
        self.name, self.part, self.seen, self.fails, self.then = name, "claude_code", seen, fails, then

    async def probe(self, ctx):
        self.seen.append(f"{self.name} probe")
        return None

    async def apply(self, ctx, state) -> bool:
        self.seen.append(f"{self.name} apply")
        if self.then is not None:
            self.then()
        if self.fails:
            raise step.Failed(f"{self.name} could not")
        return False

    def difference(self, state):
        return None


def test_a_machine_s_steps_run_in_order_and_stop_at_the_first_that_fails(job, alpha):
    seen: list[str] = []
    heard: list[jobs.Event] = []
    job.listen(heard.append)
    steps = [_Step("first", seen), _Step("second", seen, fails=True), _Step("third", seen)]
    ran = asyncio.run(step.run_steps(_ctx(job, alpha, {}), steps))
    assert ran == step.Ran("failed", (("first", False),), "second could not")
    assert seen == ["first probe", "first apply", "second probe", "second apply"]
    assert [(event.text, event.data.get("outcome"), event.data.get("why")) for event in heard] == [
        ("first", None, None),
        ("first", "done", None),
        ("second", None, None),
        ("second", "failed", "second could not"),
    ]
    assert all(event.data["part"] == "claude_code" and "stage" not in event.data for event in heard)


def test_a_run_that_is_stopping_starts_no_next_step(job, alpha):
    seen: list[str] = []
    stopping = threading.Event()
    steps = [_Step("first", seen, then=stopping.set), _Step("second", seen)]
    ran = asyncio.run(step.run_steps(_ctx(job, alpha, {}, cancelled=stopping), steps))
    assert ran == step.Ran("cancelled", (("first", False),)) and seen == ["first probe", "first apply"]


@pytest.mark.parametrize(
    ("names", "said"),
    [
        (["claude_install"], "lower-case words joined by a hyphen"),
        (["Claude"], "lower-case words joined by a hyphen"),
        (["first", "first"], "each step once"),
    ],
)
def test_steps_are_held_before_anything_runs(job, alpha, names, said):
    seen: list[str] = []
    with pytest.raises(step.runner.Refused, match=said):
        asyncio.run(step.run_steps(_ctx(job, alpha, {}), [_Step(name, seen) for name in names]))
    odd = _Step("odd", seen)
    odd.part = "claude"
    with pytest.raises(step.runner.Refused, match="one of a configure's"):
        asyncio.run(step.run_steps(_ctx(job, alpha, {}), [odd]))
    assert seen == []


# ---------------------------------------------------------------------------------------------- the settings ---
def test_the_options_read_are_claude_code_s_passed_through_with_the_role_s_own_defaults():
    defaults = yaml.safe_load(DEFAULTS.read_text())
    for name, default in claude_install.OPTIONS.items():
        option = passthrough.option(name)
        assert (option.role, option.refused) == ("claude_code", None), option
        assert defaults[name] == default
    assert passthrough.option("claude_code_install_method").choices == claude_install.METHODS
    assert all(registry.row(name).name == name for name in claude_install.ROWS)
    # The step reads these and no other: every setting it names is one of them.
    named = {
        node.value
        for node in ast.walk(ast.parse(SOURCE.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _every_setting()
    }
    assert named == {*claude_install.OPTIONS, *claude_install.ROWS}


def _every_setting() -> set[str]:
    return {row.name for row in registry.rows()} | {option.name for option in passthrough.options()}


# ---------------------------------------------------------------------------------------------- the invariant ---
def test_install_is_given_the_release_verified_never_a_channel_and_the_installer_script_is_never_named():
    source = SOURCE.read_text()
    assert "install.sh" not in source and "claude.ai/install" not in source and "bootstrap.sh" not in source
    # The script runs the binary's install of its first word, which the one call of it gives as the release verified.
    assert '"$dest" install "$1"' in claude_install.INSTALL
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and node.args and getattr(node.args[0], "id", None) == "INSTALL"
    ]
    assert len(calls) == 1 and ast.unparse(calls[0].args[1]) == "release.version"
    # No channel's name is a word of any command the step gives the machine.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in downloads.CHANNELS, node.lineno
