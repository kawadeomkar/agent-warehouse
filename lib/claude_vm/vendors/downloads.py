"""Downloads held to their vendor's signature, checked on this computer: a release of Claude Code with its digests, from
Anthropic's signed manifest, and the digest of an Ubuntu cloud image, from Ubuntu's signed checksum list
(docs/architecture/engine.md §16.7).

Today's two task files are the specification, step by step: roles/claude_code/tasks/install_native.yml and
roles/proxmox_template/tasks/image_signature.yml, whose URLs, caps, first signed release and order of checks this
module keeps, with openpgp.verify called here, directly, where today's roles call it as a filter.

Claude Code: claude_release(base, wanted) takes a channel, stable or latest, or a version. A version asked for is held
to the release's pattern and to SIGNED_SINCE, the first release Anthropic signed the manifest of, before anything is
fetched. For a channel, its pointer, <base>/<channel>, at most POINTER_MOST bytes, and the version it names held the
same way: the pointer is signed by nobody and needs no signature, since whatever it names must still verify, so
whoever serves it can only pick among Anthropic's own releases of SIGNED_SINCE or later. Then
<base>/<version>/manifest.json and manifest.json.sig, each at most DOCUMENT_MOST bytes, the signature asked for only
once the manifest is in hand; openpgp.verify with the vendored key (keys.py); and only then is the manifest read: it
must name the version it was fetched as, and the SHA-256 of each platform's binary it names is returned, as a Release.

Ubuntu: ubuntu_digest(base, release, file_name) fetches <base>/<release>/SHA256SUMS and then SHA256SUMS.gpg, each at
most DOCUMENT_MOST bytes, verifies them the same way with Ubuntu's cloud image key, and returns the digest of the one
line that names file_name, or NotListed where no line or more than one does.

Every document is read on one transport capped at DOCUMENT_MOST, so none is read past it, and its answer's status is
read before its own cap is: an error page, longer than a channel's pointer, is said as the status it came with. A
document one byte past its cap is Oversized, and nothing after it is fetched. A 3xx is an answer, never followed:
NotServed, as is any answer but 200. The transport's own errors pass as they are (Unreachable, Untrusted among them,
and TimedOut). Nothing is returned from a document whose signature has not verified, and of a document only its version
and digests are ever returned. What a server sent is said only through plain_text, cut, as JSON. The binary's download
on a machine, its install and the image's import are not this module's: the steps that use these calls are the guest's
and the template's.

Blocking. It reads no setting and no variable: the base address, the certificate file's setting (tls_ca_file) and the
proxy are its caller's (proxy.choose works out today's), and the key is keys.py's, unless a caller names its own, as
today's proxmox_template_image_key and _fingerprint name one for an image of one's own.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import types
from collections.abc import Mapping

from .. import openpgp
from ..text import plain_text
from . import keys, trust
from .transport import Answer, TooLarge, Transport

__all__ = [
    "DownloadError",
    "NotARelease",
    "NotListed",
    "NotServed",
    "NotThisRelease",
    "Oversized",
    "Release",
    "Unverified",
    "claude_release",
    "ubuntu_digest",
]

# What a request's User-Agent says: the Proxmox client's own word (pve/pin.py).
USER_AGENT = "agent-warehouse"
# The channels a release is named by, each a file under the base address that holds one version.
CHANNELS = ("stable", "latest")
# A release's version as the channels name one: today's claude_code_release_pattern, held whole with fullmatch
# (tests/unit/test_aw_downloads.py holds the two equal).
RELEASE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?")
# The first release whose manifest Anthropic signed: today's claude_code_signed_since, the oldest the role installs.
SIGNED_SINCE = "2.1.89"
# A channel's pointer at most, in bytes: today's, under 256 characters. A manifest, a signature or a checksum list at
# most, and what the transport reads of any answer: today's manifest and its signature under 1 MiB, which no release's
# is. Today's checksum list was read whole, with no cap; Ubuntu's for a release is a few kilobytes.
POINTER_MOST = 255
DOCUMENT_MOST = 1024 * 1024 - 1
# Each wait of a call, and the whole call, in seconds: today's uri waited 30 s for Anthropic's files, tried three
# times, and 60 s for Ubuntu's, once; the transport tries a GET again within the deadline.
CLAUDE_READ, CLAUDE_DEADLINE = 30.0, 90.0
UBUNTU_READ, UBUNTU_DEADLINE = 60.0, 120.0
# The files a release's folder holds, each beside its detached signature.
MANIFEST, MANIFEST_SIGNATURE = "manifest.json", "manifest.json.sig"
SUMS, SUMS_SIGNATURE = "SHA256SUMS", "SHA256SUMS.gpg"
# A SHA-256 as the manifest and the list give one; a platform as the manifest names one (linux-x64, linux-arm64,
# darwin-arm64, linux-x64-musl): the name a binary's path is made of, so letters, digits and inner dashes alone.
DIGEST = re.compile(r"[0-9a-f]{64}")
PLATFORM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# One line of a checksum list, its carriage return taken off: the digest, a space, a space or a * (binary), the name.
SUMS_LINE = re.compile(rb"([0-9a-f]{64}) [ *](.+)")
# A release's folder under Ubuntu's base address: names of letters, digits, dots, dashes and underscores, a / between
# them, and never . or ..; the image's file name: one name, no / and no control character, only ever compared.
FOLDER_NAME = re.compile(r"[A-Za-z0-9._-]+")
FILE_NAME = re.compile(r"[^/\x00-\x1f\x7f]+")
# How much of what a server named is shown, made plain, before it is quoted as JSON.
SHOWN = 100


class DownloadError(Exception):
    """A vendor's document refused, in this module's words: what was asked for, and why."""


class NotARelease(DownloadError):
    """The version asked for, or the one a channel named, is no version of SIGNED_SINCE or later: Anthropic signed no
    manifest of it. A channel's is refused before its manifest is asked for, a version asked for before anything is."""


class NotServed(DownloadError):
    """A document was answered with another status than 200: a 3xx among them, which is never followed."""


class Oversized(DownloadError):
    """A document is longer than its cap, and was read no further."""


class Unverified(DownloadError):
    """A document's detached signature does not verify by the vendored key: openpgp.verify's reason is said."""


class NotThisRelease(DownloadError):
    """Anthropic's signed manifest is not the release it was fetched as: no JSON object, another version, or no binary
    with a SHA-256."""


class NotListed(DownloadError):
    """Ubuntu's signed checksum list names the image not once: no line, or more than one, names it."""


@dataclasses.dataclass(frozen=True)
class Release:
    """A release of Claude Code as Anthropic's signed manifest gives it: its version, and the SHA-256 of its binary for
    each platform the manifest names with one, by the manifest's name for the platform. Read-only."""

    version: str
    checksums: Mapping[str, str]


def _shown(value: object) -> str:
    """What a server or a caller named, as JSON of its plain text, cut: a newline or an escape is seen, never let
    through."""
    if not isinstance(value, str):
        return f"(a value of type {type(value).__name__})"
    return json.dumps(plain_text(value)[:SHOWN])


def _signed(version: str) -> bool:
    """Whether version is a release's, of SIGNED_SINCE or later, by its first three numbers."""
    if not RELEASE.fullmatch(version):
        return False
    try:
        numbers = tuple(int(part) for part in version.split("-", 1)[0].split("."))
    except ValueError:  # more digits than the interpreter turns into a number: no release's
        return False
    return numbers >= tuple(int(part) for part in SIGNED_SINCE.split("."))


def _get(transport: Transport, base: str, path: str, what: str, most: int, deadline: float) -> bytes:
    """The body of path under base, whose answer must be a 200 of at most most bytes: the status is read first, so that
    an error page longer than a channel's pointer is said as the status it came with."""
    url = f"{base}/{path}"
    answer: Answer | None = None
    try:
        answer = transport.request("GET", path, deadline=deadline)
    except TooLarge:
        pass  # past the transport's cap, DOCUMENT_MOST, and past most with it: said below, outside the handler
    if answer is not None and answer.status != 200:
        never = ", a redirect, which is never followed" if 300 <= answer.status < 400 else ""
        raise NotServed(f"{url} answered {answer.status}{never}, not the {what}")
    if answer is None or len(answer.body) > most:
        raise Oversized(f"{url} sent more than {most} bytes, which no {what} is")
    return answer.body


def _verify(data: bytes, signature: bytes, key: keys.Key, what: str) -> None:
    """Return when signature verifies data by key; Unverified, with openpgp's reason, otherwise."""
    reason = ""
    try:
        openpgp.verify(data, signature, key.armored(), key.fingerprint)
    except openpgp.Refused as refused:
        reason = str(refused)
    if reason:
        raise Unverified(f"{what} does not verify by the key {key.fingerprint}: {reason}")


def _key(key: object) -> keys.Key:
    if not isinstance(key, keys.Key):
        raise TypeError("the key is a keys.Key: a vendored key file and its fingerprint")
    return key


def claude_release(
    base: str,
    wanted: str,
    *,
    key: keys.Key = keys.CLAUDE_CODE,
    tls_ca_file: str | os.PathLike[str] | None = None,
    proxy: str | None = None,
) -> Release:
    """The release of Claude Code wanted names, a channel (stable or latest) or a version, from Anthropic's downloads at
    base (today's claude_code_download_url), once its manifest's signature verifies by key: see the module's docstring
    for the order of the checks. Connections verify against certifi's list and the file tls_ca_file names, where it
    names one, through proxy, an http:// URL, where there is one. Raises NotARelease, NotServed, Oversized, Unverified,
    NotThisRelease, the transport's errors, and trust.Unusable where the certificates cannot be used."""
    key = _key(key)
    if not isinstance(base, str):
        raise TypeError("the downloads' address is a str")
    base = base.rstrip("/")
    if not (isinstance(wanted, str) and (wanted in CHANNELS or _signed(wanted))):
        raise NotARelease(
            f"the release asked for, {_shown(wanted)}, is not stable, latest or a version of {SIGNED_SINCE} or later,"
            " the first whose release Anthropic signs"
        )
    with Transport(
        base,
        context=trust.context(tls_ca_file),
        user_agent=USER_AGENT,
        read=CLAUDE_READ,
        most=DOCUMENT_MOST,
        proxy=proxy,
    ) as documents:
        version = wanted
        if wanted in CHANNELS:
            named = _get(documents, base, wanted, "channel's pointer", POINTER_MOST, CLAUDE_DEADLINE)
            version = named.decode("utf-8", "replace").strip()
            if not _signed(version):
                raise NotARelease(
                    f"{base}/{wanted} named {_shown(version)}, which is not a version of {SIGNED_SINCE} or later, the"
                    " first whose release Anthropic signs"
                )
        manifest = _get(documents, base, f"{version}/{MANIFEST}", "release's manifest", DOCUMENT_MOST, CLAUDE_DEADLINE)
        signature = _get(
            documents, base, f"{version}/{MANIFEST_SIGNATURE}", "manifest's signature", DOCUMENT_MOST, CLAUDE_DEADLINE
        )
    _verify(manifest, signature, key, f"{base}/{version}/{MANIFEST}")
    return _release(manifest, version)


def _release(manifest: bytes, version: str) -> Release:
    """The Release a verified manifest gives, fetched as version's."""
    try:
        parsed = json.loads(manifest)
    except ValueError:  # a JSONDecodeError, or a UnicodeDecodeError: both are ValueErrors
        parsed = None
    if not isinstance(parsed, dict):
        raise NotThisRelease(f"the manifest Anthropic signed, fetched as {version}'s, is no JSON object")
    if parsed.get("version") != version:
        raise NotThisRelease(
            f"the manifest Anthropic signed, fetched as {version}'s, names version {_shown(parsed.get('version'))}"
        )
    platforms = parsed.get("platforms")
    checksums = {
        name: entry["checksum"]
        for name, entry in (platforms.items() if isinstance(platforms, dict) else ())
        if isinstance(name, str)
        and PLATFORM.fullmatch(name)
        and isinstance(entry, dict)
        and isinstance(entry.get("checksum"), str)
        and DIGEST.fullmatch(entry["checksum"])
    }
    if not checksums:
        raise NotThisRelease(f"the manifest Anthropic signed for {version} names no binary with a SHA-256")
    return Release(version, types.MappingProxyType(checksums))


def _folder(release: object) -> str:
    if (
        not isinstance(release, str)
        or not release
        or not all(FOLDER_NAME.fullmatch(name) and name not in (".", "..") for name in release.split("/"))
    ):
        raise ValueError(
            "a release's folder is one or more names under the base address with a / between them, such as"
            " noble/release, and never . or .."
        )
    return release


def ubuntu_digest(
    base: str,
    release: str,
    file_name: str,
    *,
    key: keys.Key = keys.UBUNTU_CLOUD_IMAGE,
    tls_ca_file: str | os.PathLike[str] | None = None,
    proxy: str | None = None,
) -> str:
    """The SHA-256 of file_name, 64 lower-case hex digits, as Ubuntu's checksum list in the folder release under base
    gives it (<base>/<release>/SHA256SUMS, as today's proxmox_template_image_checksum names one), once the list's
    signature, SHA256SUMS.gpg beside it, verifies by key. Connections verify against certifi's list and the file
    tls_ca_file names, where it names one, through proxy, an http:// URL, where there is one. Raises ValueError for a
    folder or a file name that is not one, before anything is fetched; NotServed, Oversized, Unverified, NotListed, the
    transport's errors, and trust.Unusable where the certificates cannot be used."""
    key = _key(key)
    if not isinstance(base, str):
        raise TypeError("the images' address is a str")
    base = base.rstrip("/")
    folder = _folder(release)
    if not isinstance(file_name, str) or not FILE_NAME.fullmatch(file_name) or file_name in (".", ".."):
        raise ValueError("the image's file name is one name, with no / and no control character")
    with Transport(
        base,
        context=trust.context(tls_ca_file),
        user_agent=USER_AGENT,
        read=UBUNTU_READ,
        most=DOCUMENT_MOST,
        proxy=proxy,
    ) as documents:
        listing = _get(documents, base, f"{folder}/{SUMS}", "checksum list", DOCUMENT_MOST, UBUNTU_DEADLINE)
        signature = _get(
            documents, base, f"{folder}/{SUMS_SIGNATURE}", "checksum list's signature", DOCUMENT_MOST, UBUNTU_DEADLINE
        )
    url = f"{base}/{folder}/{SUMS}"
    _verify(listing, signature, key, url)
    wanted = file_name.encode("utf-8")
    digests = [
        found.group(1).decode("ascii")
        for line in listing.split(b"\n")
        if (found := SUMS_LINE.fullmatch(line.removesuffix(b"\r"))) and found.group(2) == wanted
    ]
    if len(digests) != 1:
        raise NotListed(f"Ubuntu's signed checksum list {url} names {_shown(file_name)} {len(digests)} times, not once")
    return digests[0]
