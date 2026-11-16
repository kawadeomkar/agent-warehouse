"""What a VM downloads that its vendor signs, held to the signature before it is used: Claude Code's native install
(roles/claude_code/tasks/install_native.yml) and the template's cloud image (roles/proxmox_template/tasks/
image_signature.yml), each run as the role runs it against stand-in hosts and a stand-in for the vendor's download
server (tests/molecule/fake_downloads.py). The roles are copies, each with a key made here in place of the vendor's:
the files a key signs are the tests', never a vendor's, and no private key is in the repository."""

from __future__ import annotations

import getpass
import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from harness import _failures, _local_hosts, _ran, _run, _serve
from signing import Signer

REPO = Path(__file__).resolve().parents[2]
FAKES = REPO / "tests" / "molecule"
RELEASE = "2.1.400"
# What the stand-in for Claude Code's binary does when it is run: says its version, or says what it was asked and
# installs itself as the real one's `install` does, into ~/.local/share/claude/versions/, linked from ~/.local/bin.
BINARY = f"""#!/bin/sh
[ "$1" = --version ] && {{ echo "{RELEASE} (Claude Code)"; exit 0; }}
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
IMAGE = b"not really a disk image\n"
IMAGE_NAME = "noble-minimal-cloudimg-amd64.img"


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def _manifest(version: str, binary: bytes) -> bytes:
    digest = hashlib.sha256(binary).hexdigest()
    platforms = {
        name: {"binary": "claude", "checksum": digest, "size": len(binary)} for name in ("linux-x64", "linux-arm64")
    }
    return json.dumps({"version": version, "platforms": platforms}, indent=2).encode()


def _release(
    base: Path,
    signer: Signer,
    *,
    version: str = RELEASE,
    manifest: bytes | None = None,
    binary: bytes = BINARY,
    signed: bytes | None = None,
) -> None:
    """A release under base as Anthropic lays one out: the channels, the manifest and its signature, the binaries."""
    _write(base / "stable", version + "\n")
    _write(base / "latest", version + "\n")
    body = manifest if manifest is not None else _manifest(version, binary)
    _write(base / version / "manifest.json", body)
    _write(base / version / "manifest.json.sig", signer.sign(body if signed is None else signed))
    for platform in ("linux-x64", "linux-arm64"):
        _write(base / version / platform / "claude", binary)


@pytest.fixture(scope="module")
def vendor(tmp_path_factory):
    """The download server's files, each base a way a release can be served, and the key that signs them."""
    root = tmp_path_factory.mktemp("downloads")
    signer, stranger = Signer(), Signer()
    _release(root / "good", signer)
    # The manifest changed after it was signed.
    _release(root / "tampered", signer, signed=_manifest(RELEASE, b"another binary"))
    # Signed by a key that is not the vendored one.
    _release(root / "stranger", stranger)
    # A manifest that verifies, and a binary that is not the one it names.
    _release(root / "swapped", signer, manifest=_manifest(RELEASE, BINARY), binary=b"#!/bin/sh\necho swapped\n")
    # A release whose binary, verified, installs something else.
    _release(root / "refetching", signer, binary=REFETCHING)
    # A manifest Anthropic signed for another release, served as this one's.
    _release(root / "mislabeled", signer, manifest=_manifest("2.1.399", BINARY))
    # A channel that names a release older than the first Anthropic signed, and one that names no version at all.
    _release(root / "old", signer, version="2.1.50")
    _write(root / "html" / "stable", "<html>Service unavailable</html>\n")
    # Ubuntu's: the image, the checksum list beside it and the list's signature; and a list that names another image.
    sums = f"{hashlib.sha256(IMAGE).hexdigest()} *{IMAGE_NAME}\n{'0' * 64} *other.img\n".encode()
    _write(root / "ubuntu" / IMAGE_NAME, IMAGE)
    _write(root / "ubuntu" / "SHA256SUMS", sums)
    _write(root / "ubuntu" / "SHA256SUMS.gpg", signer.sign(sums))
    _write(root / "ubuntu-unsigned" / "SHA256SUMS", sums)
    _write(root / "ubuntu-unsigned" / "SHA256SUMS.gpg", stranger.sign(sums))
    for url in _serve(FAKES / "fake_downloads.py", str(root)):
        yield {"root": root, "url": url, "signer": signer}


def _roles(root: Path, signer: Signer) -> Path:
    """Copies of the two roles, each with this test's key in place of its vendor's."""
    roles = root / "roles"
    for role, key in (("claude_code", "claude-code.asc"), ("proxmox_template", "ubuntu-cloudimage.asc")):
        shutil.copytree(REPO / "roles" / role, roles / role)
        (roles / role / "files" / key).write_text(signer.public())
    return roles


INSTALL = [
    {
        "name": "Install Claude Code as the role does",
        "hosts": "all",
        "gather_facts": True,
        "gather_subset": ["min"],
        "tasks": [
            {
                "name": "Install",
                "ansible.builtin.include_role": {"name": "claude_code", "tasks_from": "install_native"},
            }
        ],
    }
]

# Each VM: the base its downloads come from, and claude_code_version.
VMS = {
    "stable": ("good", "stable"),
    "latest": ("good", "latest"),
    "exact": ("good", RELEASE),
    "tampered": ("tampered", "stable"),
    "stranger": ("stranger", "stable"),
    "swapped": ("swapped", "stable"),
    "old-channel": ("old", "stable"),
    "old-exact": ("good", "2.1.50"),
    "html": ("html", "stable"),
    "refetching": ("refetching", "stable"),
    "mislabeled": ("mislabeled", "stable"),
}


@pytest.fixture(scope="module")
def installed(vendor, tmp_path_factory):
    root = tmp_path_factory.mktemp("install")
    roles = _roles(root, vendor["signer"])
    hosts = _local_hosts(root, VMS)
    for name, (base, version) in VMS.items():
        home = root / name
        hosts[name] |= {
            "claude_code_user": getpass.getuser(),
            "claude_code_home": str(home),
            "claude_code_bin": str(home / ".local" / "bin" / "claude"),
            "claude_code_version": version,
            "claude_code_download_url": f"{vendor['url']}/{base}",
        }
    playbook = root / "install.yml"
    playbook.write_text(yaml.safe_dump(INSTALL, sort_keys=False))
    extra = {"ansible_become": False, "claude_code_release_fingerprint": vendor["signer"].fingerprint}
    report = _run(root, playbook, hosts, "-e", json.dumps(extra), env={"ANSIBLE_ROLES_PATH": str(roles)})
    requests = (vendor["root"] / "requests").read_text().splitlines()
    return root, report, requests


def _failed(report: dict, host: str) -> str:
    """What failed host, by its last failed task, the role's name Ansible puts before an included role's task left
    out."""
    return _failures(report)[host].split(" : ", 1)[-1]


def _asked(root: Path, vm: str) -> list[str]:
    asked = root / vm / "asked"
    return asked.read_text().splitlines() if asked.exists() else []


@pytest.mark.parametrize("vm", ["stable", "latest", "exact"])
def test_the_binary_the_signed_manifest_names_is_installed_as_the_release_verified(installed, vm):
    # The release the channel named, never the channel's name, which the binary could resolve and fetch again for
    # itself: the channel is the auto-update setting the role writes.
    root, report, _ = installed
    assert vm not in _failures(report), _failures(report)
    assert _asked(root, vm) == [f"install {RELEASE}"]
    installed_binary = (root / vm / ".local" / "bin" / "claude").resolve()
    assert hashlib.sha256(installed_binary.read_bytes()).hexdigest() == hashlib.sha256(BINARY).hexdigest()
    # The download is gone, from the user's own folder, never /tmp, which may be mounted noexec.
    assert list((root / vm / ".claude" / "downloads").iterdir()) == []


def test_an_install_that_puts_another_binary_in_place_is_refused(installed):
    root, report, _ = installed
    assert _failed(report, "refetching").startswith(
        "install_native | Refuse an install that is not the release verified"
    )
    assert _asked(root, "refetching") == [f"install {RELEASE}"]


def test_the_installer_script_is_never_fetched_and_the_manifest_s_signature_always_is(installed):
    _, _, requests = installed
    assert not any(path.endswith("install.sh") for path in requests)
    assert f"/good/{RELEASE}/manifest.json.sig" in requests
    # An exact version reads no channel.
    assert requests.count("/good/stable") == 1 and requests.count("/good/latest") == 1


@pytest.mark.parametrize(
    ("vm", "why"),
    [
        ("tampered", "the data is not what was signed"),
        ("stranger", "a signature by another key than"),
    ],
)
def test_a_manifest_that_does_not_verify_stops_the_install_before_any_binary_is_fetched(installed, vm, why):
    root, report, requests = installed
    assert why in _failed(report, vm)
    assert _failed(report, vm).startswith("install_native | Verify the manifest by Anthropic's signature")
    assert not any(path.startswith(f"/{VMS[vm][0]}/{RELEASE}/linux-") for path in requests)
    assert _asked(root, vm) == []


def test_a_binary_that_is_not_the_one_the_signed_manifest_names_is_never_run(installed):
    root, report, _ = installed
    assert _failed(report, "swapped").startswith("install_native | Download the release's binary")
    assert _asked(root, "swapped") == [] and not (root / "swapped" / ".local").exists()


@pytest.mark.parametrize(
    ("vm", "task"),
    [
        ("old-channel", "install_native | Refuse a release that is no version of Anthropic's signed ones"),
        ("html", "install_native | Refuse a release that is no version of Anthropic's signed ones"),
        ("old-exact", "install_native | Refuse a version Anthropic signed no release of"),
    ],
)
def test_a_release_older_than_the_first_signed_one_or_no_version_is_refused(installed, vm, task):
    root, report, requests = installed
    assert _failed(report, vm).startswith(task), _failed(report, vm)
    assert not any(path.startswith(f"/{VMS[vm][0]}/2.1.50/") for path in requests)
    assert _asked(root, vm) == []


def test_the_download_directory_goes_whatever_happened(installed):
    _, report, _ = installed
    removed = _ran(report, "install_native | Remove the download")
    assert {"stable", "swapped"} <= set(removed)


# ---------------------------------------------------------------------------------------------- the cloud image ---

IMAGE_PLAY = [
    {
        "name": "Hold the image to its signed checksum, as the template role does",
        "hosts": "all",
        "gather_facts": False,
        "tasks": [
            {
                "name": "Verify",
                "ansible.builtin.include_role": {"name": "proxmox_template", "tasks_from": "image_signature"},
            },
            {
                "name": "Say the checksum the download is given",
                "ansible.builtin.debug": {"var": "proxmox_template_image_signed"},
            },
        ],
    }
]


@pytest.fixture(scope="module")
def imaged(vendor, tmp_path_factory):
    root = tmp_path_factory.mktemp("image")
    roles = _roles(root, vendor["signer"])
    url = vendor["url"]
    cases = {
        "signed": {"list": f"{url}/ubuntu/SHA256SUMS", "signature": f"{url}/ubuntu/SHA256SUMS.gpg"},
        "unsigned": {"list": f"{url}/ubuntu-unsigned/SHA256SUMS", "signature": f"{url}/ubuntu-unsigned/SHA256SUMS.gpg"},
        "not-listed": {
            "list": f"{url}/ubuntu/SHA256SUMS",
            "signature": f"{url}/ubuntu/SHA256SUMS.gpg",
            "image": "x.img",
        },
        "off": {"list": f"{url}/ubuntu/SHA256SUMS", "signature": ""},
        "digest": {"checksum": "sha256:" + "1" * 64, "signature": f"{url}/ubuntu/SHA256SUMS.gpg"},
        # Nothing to hold the image to: downloaded, it would be taken on trust.
        "none": {"checksum": "", "signature": ""},
    }
    hosts = _local_hosts(root, cases)
    for name, case in cases.items():
        hosts[name] |= {
            "proxmox_template_image_url": f"{url}/ubuntu/{case.get('image', IMAGE_NAME)}",
            "proxmox_template_image_checksum": case.get("checksum", f"sha256:{case.get('list')}"),
            "proxmox_template_image_signature": case["signature"],
        }
    playbook = root / "image.yml"
    playbook.write_text(yaml.safe_dump(IMAGE_PLAY, sort_keys=False))
    extra = {"ansible_become": False, "proxmox_template_image_fingerprint": vendor["signer"].fingerprint}
    report = _run(root, playbook, hosts, "-e", json.dumps(extra), env={"ANSIBLE_ROLES_PATH": str(roles)})
    said = {
        host: result.get("proxmox_template_image_signed")
        for host, results in _ran(report, "Say the checksum the download is given").items()
        for result in results
    }
    return report, said


def test_the_image_is_held_to_the_digest_the_signed_list_gives(imaged):
    report, said = imaged
    assert said["signed"] == f"sha256:{hashlib.sha256(IMAGE).hexdigest()}"


def test_with_no_signature_the_checksum_is_given_as_it_is_set(imaged, vendor):
    _, said = imaged
    assert said["off"] == f"sha256:{vendor['url']}/ubuntu/SHA256SUMS"


@pytest.mark.parametrize(
    ("host", "why"),
    [
        ("unsigned", "image_signature | Verify the checksum list by Ubuntu's signature"),
        ("not-listed", "image_signature | Refuse a list that names the image not once"),
        ("digest", "image_signature | Refuse a signature with no checksum list it signs"),
        ("none", "image_signature | Refuse an image with no checksum to hold it to"),
    ],
)
def test_a_list_that_does_not_verify_or_names_no_image_stops_the_build(imaged, host, why):
    report, said = imaged
    assert _failed(report, host).startswith(why), _failed(report, host)
    assert host not in said


@pytest.mark.parametrize(
    ("checksum", "signature"),
    [
        (
            "sha256:https://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS",
            "https://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS.gpg",
        ),
        (
            "sha256:https://cloud-images.ubuntu.com/releases/24.04/release-20261105/SHA256SUMS",
            "https://cloud-images.ubuntu.com/releases/24.04/release-20261105/SHA256SUMS.gpg",
        ),
        # Over http too: the signature holds the list, whatever carried it.
        (
            "sha256:http://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS",
            "http://cloud-images.ubuntu.com/minimal/releases/noble/release/SHA256SUMS.gpg",
        ),
        ("sha256:https://example.com/images/SHA256SUMS", ""),
        ("sha256:https://cloud-images.ubuntu.com.example.com/x/SHA256SUMS", ""),
        ("sha256:" + "1" * 64, ""),
        ("", ""),
    ],
)
def test_the_default_signature_is_ubuntu_s_own_beside_its_list_and_none_for_another_image(checksum, signature):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar, trust_as_template

    defaults = yaml.safe_load((REPO / "roles" / "proxmox_template" / "defaults" / "main.yml").read_text())
    templar = Templar(loader=DataLoader(), variables={"proxmox_template_image_checksum": checksum})
    assert templar.template(trust_as_template(defaults["proxmox_template_image_signature"])) == signature


def test_a_signed_manifest_of_another_release_is_refused(installed):
    # Signed, but of another release than the channel named: served in its place, it would install that one.
    root, report, _ = installed
    assert _failed(report, "mislabeled").startswith(
        "install_native | Refuse a manifest that is not this release's for this machine"
    )
    assert _asked(root, "mislabeled") == []


SETTINGS = [
    {
        "name": "Write Claude Code's settings as the role does",
        "hosts": "all",
        "gather_facts": False,
        "tasks": [
            {
                "name": "Settings",
                "ansible.builtin.include_role": {"name": "claude_code", "tasks_from": "settings"},
            }
        ],
    }
]


def test_the_update_channel_is_the_setting_the_role_writes(tmp_path):
    # The role installs the release it verified, never the channel's name: the channel a native install follows is
    # autoUpdatesChannel, written for stable or latest, and none for a pinned version or npm's.
    cases = {"stable": ("stable", "native"), "latest": ("latest", "native"), "pinned": (RELEASE, "native")}
    cases["npm"] = ("stable", "npm")
    hosts = _local_hosts(tmp_path, cases)
    for name, (version, method) in cases.items():
        home = tmp_path / name
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text('{"theme": "dark"}\n')
        hosts[name] |= {
            "claude_code_user": getpass.getuser(),
            "claude_code_home": str(home),
            "claude_code_version": version,
            "claude_code_install_method": method,
        }
    playbook = tmp_path / "settings.yml"
    playbook.write_text(yaml.safe_dump(SETTINGS, sort_keys=False))
    report = _run(tmp_path, playbook, hosts, "-e", json.dumps({"ansible_become": False}))
    assert report["rc"] == 0, _failures(report)
    written = {name: json.loads((tmp_path / name / ".claude" / "settings.json").read_text()) for name in cases}
    assert written["stable"] == {"theme": "dark", "autoUpdatesChannel": "stable"}
    assert written["latest"]["autoUpdatesChannel"] == "latest"
    assert written["pinned"] == written["npm"] == {"theme": "dark"}
