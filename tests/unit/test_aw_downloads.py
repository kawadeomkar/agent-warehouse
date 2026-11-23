"""Verified downloads (lib/claude_vm/vendors/downloads.py and keys.py; docs/architecture/engine.md §16.7).

Every document is served by the fake download server (tests/molecule/fake_downloads.py) over TLS, with a node's
certificate tests/unit/pki.py made, whose CA stands in for one of certifi's public roots: certifi.where points at a file
of it, as tests/unit/test_aw_vendor_trust.py does, so the client verifies the fake as it would verify Anthropic's or
Ubuntu's servers. A release and a checksum list are signed by keys tests/unit/signing.py makes, never a vendor's, or are
the vendors' own files kept in tests/unit/openpgp/, verified with the vendored keys through keys.py's table.

Each refusal is held, and for each the fake's log of the paths asked for shows nothing fetched after it: a manifest or a
list changed by one byte; a signature by another key, or dated more than a day ahead; a refusal of openpgp's with no
words; a version below the first release Anthropic signed, asked for or named by a channel; a document one byte past
its cap; a list with no line, or two, for the image; an answer that is not a 200, a redirect among them; a certificate
that does not verify; a list's or a signature's URL that is not one https:// address. A list and its signature come
from URLs of their own, on two servers, with a pre-signed URL's query, and a key given as its text. What the roles'
task files hold - the release's pattern, the first signed release, the caps, the files' names, the line of a list - is
held equal to theirs. The rule with a test of its own: of the new engine, only downloads.py verifies a signature, by
any road an import or a name read off a module has, and only keys.py names a vendored key file.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
import time
from pathlib import Path

import certifi
import pytest
import yaml
from harness import FAKES, REPO, _start
from pki import proxmox_pki
from signing import Signer
from test_aw_layout import _imported, _package, _within
from test_aw_transport import _new_engine, _rel

from claude_vm import openpgp
from claude_vm.vendors import downloads, keys
from claude_vm.vendors.downloads import (
    DOCUMENT_MOST,
    POINTER_MOST,
    NotARelease,
    NotListed,
    NotServed,
    NotThisRelease,
    Oversized,
    Release,
    Unverified,
    claude_release,
    listed_digest,
    signed_digest,
)
from claude_vm.vendors.transport import Untrusted

# The module's certificate authorities, the fake's and one that signed nothing the fake shows, and its two signing keys,
# the vendor's stand-in and a stranger's: cheap enough to make again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "other_pki", "signer", "stranger"}
FAKE = FAKES / "fake_downloads.py"
FIXTURES = Path(__file__).resolve().parent / "openpgp"
CLAUDE_VARS = REPO / "roles" / "claude_code" / "vars" / "main.yml"
INSTALL_NATIVE = REPO / "roles" / "claude_code" / "tasks" / "install_native.yml"
TEMPLATE_DEFAULTS = REPO / "roles" / "proxmox_template" / "defaults" / "main.yml"
IMAGE_SIGNATURE = REPO / "roles" / "proxmox_template" / "tasks" / "image_signature.yml"
RELEASE = "2.1.400"
DIGEST = hashlib.sha256(b"a binary of Claude Code").hexdigest()
IMAGE = "noble-minimal-cloudimg-amd64.img"
IMAGE_DIGEST = hashlib.sha256(b"not really a disk image").hexdigest()
FOLDER = "noble/release"
# The vendors' own files: Anthropic's release 2.1.311 and Ubuntu's list for Noble's minimal images, each as published.
REAL_RELEASE = "2.1.311"
REAL_IMAGE = "ubuntu-24.04-minimal-cloudimg-amd64.img"
# What each fetch of a release asks the fake for, in order, under its base's path.
POINTER = "/claude/stable"
MANIFEST = f"/claude/{RELEASE}/manifest.json"
SIGNATURE = f"/claude/{RELEASE}/manifest.json.sig"
SUMS = f"/ubuntu/{FOLDER}/SHA256SUMS"
SUMS_SIGNATURE = f"/ubuntu/{FOLDER}/SHA256SUMS.gpg"


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(scope="module")
def other_pki(tmp_path_factory):
    """A CA of another subject than the fake's: OpenSSL finds no issuer of the fake's certificate in a file of it."""
    return proxmox_pki(tmp_path_factory.mktemp("other-pki"), key_usage=True, name="Another Test CA")


@pytest.fixture(scope="module")
def signer() -> Signer:
    """The vendor's key, as the test stands in for it."""
    return Signer()


@pytest.fixture(scope="module")
def stranger() -> Signer:
    """A key that is not the vendor's."""
    return Signer()


def _certifi(monkeypatch, directory: Path, pem: str) -> None:
    """certifi's list stood in for by a file of pem: a root of certifi's, as a vendor's server's is."""
    stood_in = directory / "certifi.pem"
    stood_in.write_text(pem)
    monkeypatch.setattr(certifi, "where", lambda: str(stood_in))


@dataclasses.dataclass
class Vendor:
    """The fake download server at base, over TLS, serving root's files, and the key file of the test's signer."""

    root: Path
    base: str
    key: keys.Key

    def put(self, path: str, data: bytes | str) -> None:
        where = self.root / path
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(data.encode() if isinstance(data, str) else data)

    def asked(self) -> list[str]:
        """Every path asked of the fake, in order."""
        log = self.root / "requests"
        return log.read_text().splitlines() if log.exists() else []

    def release(self, signer: Signer, version: str = RELEASE, *, manifest: bytes | None = None, **how) -> bytes:
        """A release under claude/ as Anthropic lays one out: the channels, the manifest and its detached signature,
        signed by signer as how says. Returns the manifest."""
        body = _manifest(version) if manifest is None else manifest
        self.put("claude/stable", version + "\n")
        self.put("claude/latest", version + "\n")
        self.put(f"claude/{version}/manifest.json", body)
        self.put(f"claude/{version}/manifest.json.sig", signer.sign(body, **how))
        return body

    def sums(self, signer: Signer, listing: bytes | None = None, **how) -> bytes:
        """A checksum list in ubuntu/<FOLDER>, beside its signature by signer. Returns the list."""
        body = _sums() if listing is None else listing
        self.put(f"ubuntu/{FOLDER}/SHA256SUMS", body)
        self.put(f"ubuntu/{FOLDER}/SHA256SUMS.gpg", signer.sign(body, **how))
        return body

    def claude(self, wanted: str = "stable", **kwargs) -> Release:
        return claude_release(f"{self.base}/claude", wanted, **{"key": self.key, **kwargs})

    def ubuntu(self, file_name: str = IMAGE, folder: str = FOLDER, **kwargs) -> str:
        listed = f"{self.base}/ubuntu/{folder}/SHA256SUMS"
        return signed_digest(listed, listed + ".gpg", file_name, **{"key": self.key, **kwargs})


def _served(root: Path, pki: dict):
    """The fake download server over TLS, serving root's files: its process and its address."""
    root.mkdir()
    server, port = _start(FAKE, str(root), "--tls", str(pki["cert"]), str(pki["key"]))
    return server, f"https://127.0.0.1:{port}"


@pytest.fixture
def vendor(tmp_path, pki, signer, monkeypatch):
    _certifi(monkeypatch, tmp_path, pki["ca"])
    key = tmp_path / "vendor.asc"
    key.write_text(signer.public())
    server, base = _served(tmp_path / "served", pki)
    try:
        yield Vendor(tmp_path / "served", base, keys.Key(key, signer.fingerprint))
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture
def second(tmp_path, pki):
    """A second download server, at another address: a list's signature may be served by another host than the list."""
    server, base = _served(tmp_path / "second", pki)
    try:
        yield Vendor(tmp_path / "second", base, keys.UBUNTU_CLOUD_IMAGE)
    finally:
        server.terminate()
        server.wait(timeout=10)


def _manifest(version: str = RELEASE, platforms: dict | None = None) -> bytes:
    """A release's manifest as Anthropic writes one: its version, and each platform's binary with its SHA-256."""
    if platforms is None:
        platforms = {
            name: {"binary": "claude", "checksum": DIGEST, "size": 23} for name in ("linux-x64", "linux-arm64")
        }
    return json.dumps({"version": version, "platforms": platforms}, indent=2).encode()


def _sums(*lines: str) -> bytes:
    """A checksum list as Ubuntu writes one: the image's line among others, each name after a space and a *."""
    lines = lines or (f"{'0' * 64} *{IMAGE}.manifest", f"{IMAGE_DIGEST} *{IMAGE}", f"{'1' * 64} *other.img")
    return "".join(line + "\n" for line in lines).encode()


def _changed(data: bytes, at: int = 10) -> bytes:
    """data with one byte changed."""
    return data[:at] + bytes([data[at] ^ 1]) + data[at + 1 :]


# ------------------------------------------------------------------------------------- what is returned ----


@pytest.mark.parametrize(
    ("wanted", "asked"),
    [
        ("stable", [POINTER, MANIFEST, SIGNATURE]),
        ("latest", ["/claude/latest", MANIFEST, SIGNATURE]),
        (RELEASE, [MANIFEST, SIGNATURE]),
    ],
)
def test_a_release_whose_manifest_the_key_signed_is_returned_with_its_digests(vendor, signer, wanted, asked) -> None:
    vendor.release(signer)
    release = vendor.claude(wanted)
    assert release == Release(RELEASE, {"linux-x64": DIGEST, "linux-arm64": DIGEST})
    assert vendor.asked() == asked
    with pytest.raises(TypeError):
        release.checksums["linux-x64"] = "0" * 64  # type: ignore[index]


def test_anthropic_s_own_release_verifies_by_the_vendored_key(vendor) -> None:
    manifest = (FIXTURES / f"claude-code-{REAL_RELEASE}-manifest.json").read_bytes()
    vendor.put("claude/stable", REAL_RELEASE + "\n")
    vendor.put(f"claude/{REAL_RELEASE}/manifest.json", manifest)
    vendor.put(
        f"claude/{REAL_RELEASE}/manifest.json.sig",
        (FIXTURES / f"claude-code-{REAL_RELEASE}-manifest.json.sig").read_bytes(),
    )
    release = claude_release(f"{vendor.base}/claude", "stable")
    platforms = json.loads(manifest)["platforms"]
    assert release.version == REAL_RELEASE and len(platforms) == 8
    assert dict(release.checksums) == {name: entry["checksum"] for name, entry in platforms.items()}
    # One byte of Anthropic's manifest changed: the vendored key refuses it.
    vendor.put(f"claude/{REAL_RELEASE}/manifest.json", _changed(manifest, 100))
    with pytest.raises(Unverified, match="the data is not what was signed\\Z"):
        claude_release(f"{vendor.base}/claude", "stable")


def test_ubuntu_s_own_list_verifies_by_the_vendored_key(vendor) -> None:
    listing = (FIXTURES / "ubuntu-SHA256SUMS").read_bytes()
    vendor.put(f"ubuntu/{FOLDER}/SHA256SUMS", listing)
    vendor.put(f"ubuntu/{FOLDER}/SHA256SUMS.gpg", (FIXTURES / "ubuntu-SHA256SUMS.gpg").read_bytes())
    (expected,) = _today_s_digests(listing, REAL_IMAGE)
    listed = f"{vendor.base}{SUMS}"
    assert signed_digest(listed, listed + ".gpg", REAL_IMAGE) == expected
    assert vendor.asked() == [SUMS, SUMS_SIGNATURE]
    vendor.put(f"ubuntu/{FOLDER}/SHA256SUMS", _changed(listing, 100))
    with pytest.raises(Unverified, match="the data is not what was signed\\Z"):
        signed_digest(listed, listed + ".gpg", REAL_IMAGE)


def test_the_image_s_digest_comes_from_the_one_line_that_names_it(vendor, signer) -> None:
    vendor.sums(signer)
    assert vendor.ubuntu() == IMAGE_DIGEST
    assert vendor.asked() == [SUMS, SUMS_SIGNATURE]


def test_a_list_and_its_signature_from_two_hosts_of_their_own_names_verify(vendor, second, signer) -> None:
    # A person's own signed image: a list of another name, with a pre-signed URL's query, and its signature of another
    # name on another server. Each document is fetched from its own URL, the query sent as it was given.
    listing = vendor.sums(signer)
    vendor.put("images/CHECKSUMS", listing)
    second.put("keys/CHECKSUMS.sig", signer.sign(listing))
    listed = f"{vendor.base}/images/CHECKSUMS?X-Signature=a%2Bb~c&expires=1"
    digest = signed_digest(listed, f"{second.base}/keys/CHECKSUMS.sig", IMAGE, key=vendor.key)
    assert digest == IMAGE_DIGEST
    assert vendor.asked() == ["/images/CHECKSUMS?X-Signature=a%2Bb~c&expires=1"]
    assert second.asked() == ["/keys/CHECKSUMS.sig"]


def test_a_key_given_as_its_text_verifies_with_the_fingerprint_it_is_found_by(vendor, signer, stranger) -> None:
    vendor.sums(signer)
    spaced = " ".join(signer.fingerprint[at : at + 4] for at in range(0, 40, 4)).lower()
    assert vendor.ubuntu(key=signer.public(), fingerprint=spaced) == IMAGE_DIGEST
    # Another key's text, by its own fingerprint: the list is not signed by it.
    with pytest.raises(Unverified, match=f"does not verify by the key {stranger.fingerprint}: a signature by another"):
        vendor.ubuntu(key=stranger.public(), fingerprint=stranger.fingerprint)


# ------------------------------------------------------------------------------------- what is refused ----


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        ("changed", "the data is not what was signed"),
        ("stranger", "a signature by another key than {fingerprint}"),
        ("ahead", "a signature made after now, by this machine's clock"),
    ],
)
def test_a_manifest_whose_signature_does_not_verify_is_refused_after_its_signature(
    vendor, signer, stranger, make, reason
) -> None:
    if make == "changed":
        body = vendor.release(signer)
        vendor.put(f"claude/{RELEASE}/manifest.json", _changed(body))
    elif make == "stranger":
        vendor.release(stranger)
    else:
        vendor.release(signer, head=signer.head(created=int(time.time()) + 2 * 86400))
    with pytest.raises(Unverified) as refused:
        vendor.claude()
    said = reason.format(fingerprint=signer.fingerprint)
    assert str(refused.value) == (
        f"{vendor.base}/claude/{RELEASE}/manifest.json does not verify by the key {signer.fingerprint}: {said}"
    )
    assert vendor.asked() == [POINTER, MANIFEST, SIGNATURE]


@pytest.mark.parametrize("make", ["changed", "stranger", "ahead"])
def test_a_checksum_list_whose_signature_does_not_verify_is_refused(vendor, signer, stranger, make) -> None:
    if make == "changed":
        vendor.put(f"ubuntu/{FOLDER}/SHA256SUMS", _changed(vendor.sums(signer)))
    elif make == "stranger":
        vendor.sums(stranger)
    else:
        vendor.sums(signer, head=signer.head(created=int(time.time()) + 2 * 86400))
    with pytest.raises(Unverified, match=f"\\A{re.escape(vendor.base)}/ubuntu/{FOLDER}/SHA256SUMS does not verify"):
        vendor.ubuntu()
    assert vendor.asked() == [SUMS, SUMS_SIGNATURE]


def test_a_signature_made_a_little_after_now_is_no_forgery(vendor, signer) -> None:
    # An hour or two of skew between the clocks is not a day.
    vendor.release(signer, head=signer.head(created=int(time.time()) + 3600))
    assert vendor.claude().version == RELEASE


@pytest.mark.parametrize("version", ["2.1.50", "2.1.88", "2.0.400", "1.9.999", "2.1.88-beta.1"])
def test_a_version_below_the_first_signed_release_is_refused_before_it_is_fetched(vendor, signer, version) -> None:
    vendor.release(signer, version)
    with pytest.raises(NotARelease) as asked:
        vendor.claude(version)
    assert str(asked.value) == (
        f'the release asked for, "{version}", is not stable, latest or a version of 2.1.89 or later, the first whose'
        " release Anthropic signs"
    )
    assert vendor.asked() == []
    # Named by a channel, it is refused once the pointer is read, before its manifest is asked for.
    with pytest.raises(NotARelease) as named:
        vendor.claude("stable")
    assert str(named.value) == (
        f'{vendor.base}/claude/stable named "{version}", which is not a version of 2.1.89 or later, the first whose'
        " release Anthropic signs"
    )
    assert vendor.asked() == [POINTER]


@pytest.mark.parametrize("version", ["2.1.89", "2.1.89-beta.1", "2.2.0", "3.0.0", "10.0.0"])
def test_the_first_signed_release_and_any_later_one_is_asked_for(vendor, signer, version) -> None:
    vendor.release(signer, version)
    assert vendor.claude(version).version == version


@pytest.mark.parametrize(
    "wanted",
    [
        "",
        "Stable",
        "stable\n",
        f"{RELEASE}\n",
        f" {RELEASE}",
        "2.1",
        "v2.1.400",
        f"../{RELEASE}",
        f"{RELEASE}/x",
        "2.1.400-",
        "2.1.400-b/c",
        "２.1.400",
        "9" * 5000 + ".1.1",
        7,
        None,
    ],
)
def test_what_is_no_channel_and_no_version_is_refused_before_anything_is_fetched(vendor, wanted) -> None:
    with pytest.raises(NotARelease, match="is not stable, latest or a version of 2.1.89 or later"):
        vendor.claude(wanted)
    assert vendor.asked() == []


def test_a_channel_that_names_no_version_is_refused_with_its_words_plain(vendor, signer) -> None:
    vendor.release(signer)
    vendor.put("claude/stable", "<html>\x1b[31mService\nunavailable</html>\n")
    with pytest.raises(NotARelease) as named:
        vendor.claude()
    assert str(named.value) == (
        f'{vendor.base}/claude/stable named "<html>Service\\nunavailable</html>", which is not a version of 2.1.89 or'
        " later, the first whose release Anthropic signs"
    )
    assert vendor.asked() == [POINTER]


def test_a_pointer_one_byte_past_its_cap_is_refused_and_read_no_further(vendor, signer) -> None:
    vendor.release(signer)
    # At the cap, the version and the space after it: today's trim takes the space off.
    vendor.put("claude/stable", RELEASE + " " * (POINTER_MOST - len(RELEASE)))
    assert vendor.claude().version == RELEASE
    vendor.put("claude/stable", RELEASE + " " * (POINTER_MOST + 1 - len(RELEASE)))
    with pytest.raises(Oversized) as refused:
        vendor.claude()
    assert str(refused.value) == f"{vendor.base}/claude/stable sent more than 255 bytes, which no channel's pointer is"
    assert vendor.asked() == [POINTER, MANIFEST, SIGNATURE, POINTER]


def test_a_manifest_one_byte_past_its_cap_is_refused_before_its_signature_is_asked_for(vendor, signer) -> None:
    padded = _manifest()
    padded += b" " * (DOCUMENT_MOST - len(padded))
    vendor.release(signer, manifest=padded)
    assert vendor.claude().version == RELEASE
    vendor.release(signer, manifest=padded + b" ")
    with pytest.raises(Oversized) as refused:
        vendor.claude()
    assert str(refused.value) == (
        f"{vendor.base}/claude/{RELEASE}/manifest.json sent more than {DOCUMENT_MOST} bytes, which no release's"
        " manifest is"
    )
    assert vendor.asked() == [POINTER, MANIFEST, SIGNATURE, POINTER, MANIFEST]


def test_a_signature_one_byte_past_its_cap_is_refused(vendor, signer) -> None:
    vendor.release(signer)
    vendor.put(f"claude/{RELEASE}/manifest.json.sig", b"-" * (DOCUMENT_MOST + 1))
    with pytest.raises(
        Oversized, match="manifest.json.sig sent more than 1048575 bytes, which no manifest's signature"
    ):
        vendor.claude()
    assert vendor.asked() == [POINTER, MANIFEST, SIGNATURE]


def test_a_checksum_list_one_byte_past_its_cap_is_refused_before_its_signature_is_asked_for(vendor, signer) -> None:
    listing = _sums()
    padded = listing + b"\n" * (DOCUMENT_MOST - len(listing))
    vendor.sums(signer, padded)
    assert vendor.ubuntu() == IMAGE_DIGEST
    vendor.sums(signer, padded + b"\n")
    with pytest.raises(Oversized, match="SHA256SUMS sent more than 1048575 bytes, which no checksum list is\\Z"):
        vendor.ubuntu()
    assert vendor.asked() == [SUMS, SUMS_SIGNATURE, SUMS]


@pytest.mark.parametrize(
    ("lines", "times"),
    [
        ((f"{'0' * 64} *other.img",), 0),
        # A name the image's is the start or the end of is not the image's.
        ((f"{IMAGE_DIGEST} *{IMAGE}.manifest", f"{IMAGE_DIGEST} *x{IMAGE}", f"{IMAGE_DIGEST} *{IMAGE[:-1]}"), 0),
        # Upper-case hex, or a digest one digit short, is no line of a list.
        ((f"{IMAGE_DIGEST.upper()} *{IMAGE}", f"{IMAGE_DIGEST[:-1]} *{IMAGE}"), 0),
        ((f"{IMAGE_DIGEST} *{IMAGE}", f"{'2' * 64}  {IMAGE}"), 2),
    ],
)
def test_a_checksum_list_that_names_the_image_not_once_is_refused(vendor, signer, lines, times) -> None:
    vendor.sums(signer, _sums(*lines))
    with pytest.raises(NotListed) as refused:
        vendor.ubuntu()
    assert str(refused.value) == (
        f'the signed checksum list {vendor.base}/ubuntu/{FOLDER}/SHA256SUMS names "{IMAGE}" {times} times, not once'
    )
    assert vendor.asked() == [SUMS, SUMS_SIGNATURE]


def test_an_unsigned_list_is_read_by_the_same_line_reader_and_no_signature_is_asked_for(vendor, signer) -> None:
    # An image held to its checksum alone, sha256:<the URL of a list>: the same lines, the same refusals.
    vendor.sums(signer)
    assert listed_digest(f"{vendor.base}{SUMS}", IMAGE) == IMAGE_DIGEST
    assert vendor.asked() == [SUMS]
    vendor.sums(signer, _sums(f"{IMAGE_DIGEST} *{IMAGE}", f"{'2' * 64}  {IMAGE}"))
    with pytest.raises(NotListed) as refused:
        listed_digest(f"{vendor.base}{SUMS}?token=x", IMAGE)
    assert str(refused.value) == f'the checksum list {vendor.base}{SUMS}?… names "{IMAGE}" 2 times, not once'


@pytest.mark.parametrize(
    ("manifest", "said"),
    [
        (_manifest("2.1.399"), 'names version "2.1.399"'),
        (json.dumps({"platforms": {}}).encode(), "names version (a value of type NoneType)"),
        (b"[]", "is no JSON object"),
        (b"\xff not json", "is no JSON object"),
        (_manifest(platforms={}), "names no binary with a SHA-256"),
        (_manifest(platforms={"linux-x64": {"checksum": DIGEST.upper()}}), "names no binary with a SHA-256"),
    ],
)
def test_a_signed_manifest_that_is_not_this_release_s_is_refused(vendor, signer, manifest, said) -> None:
    vendor.release(signer, manifest=manifest)
    with pytest.raises(NotThisRelease, match=re.escape(said)):
        vendor.claude(RELEASE)
    assert vendor.asked() == [MANIFEST, SIGNATURE]


def test_a_platform_the_manifest_names_with_no_sha_256_or_by_no_platform_s_name_is_left_out(vendor, signer) -> None:
    platforms = {
        "linux-x64": {"checksum": DIGEST},
        "linux-arm64": {"checksum": "sha256:" + DIGEST},
        "linux-x64-musl": {"checksum": DIGEST + "0"},
        "darwin-x64": {"checksum": DIGEST + "\n"},
        "linux-x64/x": {"checksum": DIGEST},
        "../linux": {"checksum": DIGEST},
        "Linux-X64": {"checksum": DIGEST},
        "darwin-arm64": "not an entry",
        "win32-x64": {"binary": "claude.exe"},
    }
    vendor.release(signer, manifest=_manifest(platforms=platforms))
    assert vendor.claude(RELEASE).checksums == {"linux-x64": DIGEST}


@pytest.mark.parametrize("missing", ["claude/stable", f"claude/{RELEASE}/manifest.json.sig"])
def test_a_document_not_served_is_refused_by_its_status(vendor, signer, missing) -> None:
    # http.server's error page is longer than a channel's pointer: the status it came with is said, never the cap.
    vendor.release(signer)
    (vendor.root / missing).unlink()
    with pytest.raises(NotServed, match=f"/{re.escape(missing)} answered 404, not the "):
        vendor.claude()
    asked = [POINTER, MANIFEST, SIGNATURE]
    assert vendor.asked() == asked[: asked.index(f"/{missing}") + 1]


def test_a_redirect_is_an_answer_and_never_followed(vendor, signer) -> None:
    vendor.release(signer)
    (vendor.root / "claude" / "stable").unlink()
    (vendor.root / "claude" / "stable").mkdir()  # http.server answers a folder's path with no / by a 301 to it with one
    with pytest.raises(NotServed) as refused:
        vendor.claude()
    assert str(refused.value) == (
        f"{vendor.base}/claude/stable answered 301, a redirect, which is never followed, not the channel's pointer"
    )
    assert vendor.asked() == [POINTER]


def test_a_certificate_that_does_not_verify_sends_nothing(
    vendor, signer, pki, other_pki, tmp_path, monkeypatch
) -> None:
    vendor.release(signer)
    vendor.sums(signer)
    other = tmp_path / "other"
    other.mkdir()
    _certifi(monkeypatch, other, other_pki["ca"])
    for call in (vendor.claude, vendor.ubuntu, lambda: listed_digest(f"{vendor.base}{SUMS}", IMAGE)):
        with pytest.raises(Untrusted):
            call()
    assert vendor.asked() == []
    # The file the setting tls_ca_file names adds the fake's root to certifi's list: then it verifies.
    roots = tmp_path / "roots.pem"
    roots.write_text(pki["ca"])
    assert vendor.claude(tls_ca_file=roots).version == RELEASE
    assert vendor.ubuntu(tls_ca_file=str(roots)) == IMAGE_DIGEST


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("http://{host}" + SUMS, "is an https:// address, fetched over https only"),
        ("ftp://{host}" + SUMS, "is an https:// address, fetched over https only"),
        ("https://" + SUMS, "is an https:// address, fetched over https only"),
        ("https://127.0.0.1:0" + SUMS, "is an https:// address, fetched over https only"),
        ("https://127.0.0.1:99999" + SUMS, "is an https:// address, fetched over https only"),
        ("https://user:pass@{host}" + SUMS, "has no user, password or fragment"),
        ("https://{host}" + SUMS + "#x", "has no user, password or fragment"),
        ("https://{host}" + SUMS + " x", "one word of visible ASCII"),
        ("https://{host}" + SUMS + "\n", "one word of visible ASCII"),
        ("https://{host}" + SUMS + "\t", "one word of visible ASCII"),
        ("", "one word of visible ASCII"),
    ],
)
def test_a_list_s_or_a_signature_s_url_that_is_not_one_is_refused_before_anything_is_fetched(vendor, signer, url, said):
    vendor.sums(signer)
    host = vendor.base.removeprefix("https://")
    good = f"{vendor.base}{SUMS}"
    for listed, signature in ((url.format(host=host), good + ".gpg"), (good, url.format(host=host))):
        with pytest.raises(ValueError, match=re.escape(said)):
            signed_digest(listed, signature, IMAGE, key=vendor.key)
    with pytest.raises(ValueError, match=re.escape(said)):
        listed_digest(url.format(host=host), IMAGE)
    with pytest.raises(TypeError, match="URL is a str"):
        signed_digest(None, good + ".gpg", IMAGE, key=vendor.key)
    assert vendor.asked() == []


@pytest.mark.parametrize("file_name", ["", ".", "..", "a/b", f"{IMAGE}\n", f"{IMAGE}\r", "\x1b[0m", 7, None])
def test_a_file_name_that_is_not_one_is_refused_before_anything_is_fetched(vendor, signer, file_name) -> None:
    vendor.sums(signer)
    with pytest.raises(ValueError, match="the image's file name is one name, with no / and no control character"):
        vendor.ubuntu(file_name)
    assert vendor.asked() == []


def test_a_base_that_is_not_https_or_a_key_that_is_not_one_is_refused(vendor, signer) -> None:
    vendor.release(signer)
    vendor.sums(signer)
    plain = vendor.base.replace("https://", "http://")
    with pytest.raises(ValueError, match="the transport takes an https:// address only"):
        claude_release(f"{plain}/claude", RELEASE, key=vendor.key)
    refusals = [
        (lambda: vendor.claude(key=vendor.key.armored()), TypeError, "the key is a keys.Key"),
        (lambda: vendor.ubuntu(key=7), TypeError, "the key is a keys.Key, or the armored text"),
        (lambda: vendor.ubuntu(fingerprint=signer.fingerprint), TypeError, "names its own fingerprint"),
        (lambda: vendor.ubuntu(key=signer.fingerprint), ValueError, "not one ASCII-armored PGP public key block"),
        (lambda: vendor.ubuntu(key=signer.public()), ValueError, "comes with the fingerprint it is found by"),
        (lambda: vendor.ubuntu(key=signer.public(), fingerprint="ABCD"), ValueError, "40 hex digits"),
    ]
    for call, error, said in refusals:
        with pytest.raises(error, match=re.escape(said)):
            call()
    assert vendor.asked() == []


def test_a_refusal_of_openpgp_s_with_no_words_is_a_refusal(vendor, stranger, monkeypatch) -> None:
    # Whether a document verified is decided by whether openpgp.verify returned, never by what its refusal says: a
    # Refused with no words, which no raise of today's openpgp.py makes, would otherwise hand back a stranger's release.
    vendor.release(stranger)
    vendor.sums(stranger)

    def wordless(*args, **kwargs) -> None:
        raise openpgp.Refused()

    monkeypatch.setattr(openpgp, "verify", wordless)
    for call in (vendor.claude, vendor.ubuntu):
        with pytest.raises(Unverified, match=r"does not verify by the key [0-9A-F]{40}: no reason given\Z"):
            call()


# ------------------------------------------------------------------------ held equal to today's roles ----


def test_the_release_s_pattern_and_the_first_signed_release_are_the_role_s() -> None:
    held = yaml.safe_load(CLAUDE_VARS.read_text())
    assert f"^{downloads.RELEASE.pattern}\\Z" == held["claude_code_release_pattern"]
    assert downloads.SIGNED_SINCE == str(held["claude_code_signed_since"])
    assert keys.CLAUDE_CODE.fingerprint == held["claude_code_release_fingerprint"]


def test_the_caps_and_the_files_are_the_install_s() -> None:
    text = INSTALL_NATIVE.read_text()
    # The channel's answer under 256 characters; the manifest and its signature under 1 MiB.
    assert re.findall(r"\| length < ([0-9]+)", text) == [str(POINTER_MOST + 1)]
    assert re.findall(r"\| max < ([0-9]+)", text) == [str(DOCUMENT_MOST + 1)]
    assert "loop: [manifest.json, manifest.json.sig]" in text
    assert (downloads.MANIFEST, downloads.MANIFEST_SIGNATURE) == ("manifest.json", "manifest.json.sig")
    assert set(downloads.CHANNELS) == {"stable", "latest"} and "claude_code_version in ['stable', 'latest']" in text


def test_the_list_and_its_signature_are_the_template_s() -> None:
    # Today's settings give the list and its signature as two URLs, which signed_digest takes as they are: the list's
    # (sha256:<its URL>), and the signature's, <the list's URL>.gpg by default for Ubuntu's own.
    defaults = yaml.safe_load(TEMPLATE_DEFAULTS.read_text())
    listed = defaults["proxmox_template_image_checksum"].removeprefix("sha256:")
    assert downloads._split(listed, "checksum list") == (
        "https://cloud-images.ubuntu.com",
        "/minimal/releases/noble/release/SHA256SUMS",
    )
    assert "~ '.gpg'" in defaults["proxmox_template_image_signature"]
    assert keys.UBUNTU_CLOUD_IMAGE.fingerprint == defaults["proxmox_template_image_fingerprint"]
    # The line today's role reads the digest from, by its own pattern.
    assert "regex_findall('(?m)^([0-9a-f]{64}) [ *]' ~" in IMAGE_SIGNATURE.read_text()


def _today_s_digests(listing: bytes, name: str) -> list[str]:
    """The digests today's role reads from a list for name: image_signature.yml's pattern, in Python."""
    return re.findall(rf"^([0-9a-f]{{64}}) [ *]{re.escape(name)}\r?$", listing.decode("utf-8"), re.M)


@pytest.mark.parametrize(
    "listing",
    [
        _sums(),
        _sums(f"{IMAGE_DIGEST}  {IMAGE}"),
        "".join(f"{line}\r\n" for line in (f"{'0' * 64} *a.img", f"{IMAGE_DIGEST} *{IMAGE}")).encode(),
        _sums(f"{IMAGE_DIGEST} *{IMAGE}").rstrip(b"\n"),
        _sums(f"{IMAGE_DIGEST} *{IMAGE} ", f"{'3' * 64} *{IMAGE}"),
    ],
)
def test_the_line_read_is_the_one_today_s_role_reads(vendor, signer, listing) -> None:
    vendor.sums(signer, listing)
    assert [vendor.ubuntu()] == _today_s_digests(listing, IMAGE)


# ----------------------------------------------------------------------------------------- the rules ----

# The one module of the new engine that verifies a vendor's signature, with why.
VERIFIERS = {
    "lib/claude_vm/vendors/downloads.py": "Claude Code's manifest and Ubuntu's checksum list, each held to its"
    " signature before anything of it is returned: E12 and F10 call it, never openpgp itself",
}
# The vendored key files, which only keys.py names.
KEY_FILES = ("claude-code.asc", "ubuntu-cloudimage.asc")


def _verifies(text: str, path: Path) -> bool:
    """Whether a module reaches today's openpgp: by any road an import statement has, by a name of downloads.py's
    taken from it (its openpgp or verify), or by openpgp read off any name (downloads.openpgp.verify)."""
    tree = ast.parse(text)
    if any(_within(module, ("openpgp",)) for module in _imported(tree, _package(path), every=True)):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "openpgp":
            return True
        if isinstance(node, ast.ImportFrom) and any(alias.name in ("openpgp", "verify") for alias in node.names):
            if any(_within(module, ("vendors.downloads",)) for module in _imported(node, _package(path))):
                return True
    return False


def test_only_downloads_verifies_a_signature_and_only_keys_names_a_key_file() -> None:
    verifiers, naming = set(), set()
    for path in _new_engine():
        text = path.read_text()
        if path.suffix == ".py" and _verifies(text, path):
            verifiers.add(_rel(path))
        strings = [node.value for node in ast.walk(ast.parse(text)) if isinstance(node, ast.Constant)]
        if any(isinstance(value, str) and any(name in value for name in KEY_FILES) for value in strings):
            naming.add(_rel(path))
    assert verifiers == set(VERIFIERS)
    assert naming == {"lib/claude_vm/vendors/keys.py"}


@pytest.mark.parametrize(
    "source",
    [
        "from .. import openpgp",
        "from ..openpgp import verify",
        "from claude_vm import openpgp",
        "from claude_vm.openpgp import Refused",
        "import claude_vm.openpgp",
        "def f():\n    from .. import openpgp\n",
        "from claude_vm.vendors.downloads import openpgp",
        "from ..vendors.downloads import openpgp",
        "from ..vendors.downloads import verify",
        "from ..vendors import downloads\ndownloads.openpgp.verify(data, signature, key, fingerprint)",
    ],
)
def test_the_scan_sees_each_way_to_openpgp(source) -> None:
    assert _verifies(source, REPO / "lib" / "claude_vm" / "engine" / "x.py")
    assert not _verifies(
        "from . import keys\nfrom ..text import plain_text", REPO / "lib" / "claude_vm" / "vendors" / "x.py"
    )
    assert not _verifies(
        "from ..vendors.downloads import signed_digest", REPO / "lib" / "claude_vm" / "engine" / "x.py"
    )


def test_every_vendored_key_is_read_from_where_today_s_role_keeps_it() -> None:
    for name, key in keys.KEYS.items():
        assert key.file.is_relative_to(REPO / "roles") and key.file.parent.name == "files", name
        assert key.armored().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"), name
        assert re.fullmatch(r"[0-9A-F]{40}", key.fingerprint), name
