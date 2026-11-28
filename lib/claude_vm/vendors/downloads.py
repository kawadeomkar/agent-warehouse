"""Downloads held to their vendor's signature, checked on this computer: a release of Claude Code with its digests, from
Anthropic's signed manifest, and the digest of a cloud image, from a signed checksum list - Ubuntu's, or a person's own
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

An image: signed_digest(list_url, signature_url, file_name) fetches the checksum list and then its detached signature,
each from a URL of its own over https (two hosts, any name, a query of a pre-signed URL kept), each through the proxy of
its own URL (list_proxy, signature_proxy), each at most DOCUMENT_MOST bytes, verifies the list by the key - Ubuntu's
cloud image key by default, or a person's own, as a keys.Key or its armored text with the fingerprint of a primary key
in it - and returns the digest of the one line that names file_name, or NotListed where no line or more than one does.
listed_digest(list_url, file_name) reads an unsigned list, the sha256:<URL of a list> form of an image held to its
checksum alone, with the same line reader.

Every document is read on a transport of its own URL's host capped at DOCUMENT_MOST, so none is read past it, and its
answer's status is read before its own cap is: an error page, longer than a channel's pointer, is said as the status it
came with. A document one byte past its cap is Oversized, and nothing after it is fetched. A 3xx is an answer, never
followed: NotServed, as is any answer but 200. A document the transport could not fetch is NotFetched, which names the
document and its URL before the transport's own words, and keeps the transport's error (Unreachable, Untrusted among
them, and TimedOut) as its error. Nothing is returned from a document whose signature has not verified - decided by
whether openpgp.verify returned, never by what a refusal says - and of a document only its version and digests are ever
returned. What a server sent is said only through plain_text, cut, as JSON, and a URL is said without its query, which
may carry a pre-signed URL's signature. The binary's download on a machine, its install and the image's import are not
this module's: the steps that use these calls are the guest's and the template's.

Blocking. It reads no setting and no variable: the addresses, the certificate file's setting (tls_ca_file) and the
proxies are its caller's (proxy.choose gives the one for each URL), and the key is keys.py's, unless a caller names its
own, as today's proxmox_template_image_key and _fingerprint name one for an image of one's own.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import types
import urllib.parse
from collections.abc import Mapping

from .. import openpgp
from ..text import plain_text
from . import keys, trust
from .transport import Answer, TooLarge, Transport, TransportError

__all__ = [
    "DownloadError",
    "NotARelease",
    "NotFetched",
    "NotListed",
    "NotServed",
    "NotThisRelease",
    "Oversized",
    "Release",
    "Unverified",
    "claude_release",
    "listed_digest",
    "signed_digest",
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
# is, and a checksum list, which for a release of Ubuntu's is a few kilobytes.
POINTER_MOST = 255
DOCUMENT_MOST = 1024 * 1024 - 1
# Each wait of a call, and the whole call, in seconds: today's uri waited 30 s for Anthropic's files, tried three
# times, and 60 s for a checksum list, once; the transport tries a GET again within the deadline.
CLAUDE_READ, CLAUDE_DEADLINE = 30.0, 90.0
LIST_READ, LIST_DEADLINE = 60.0, 120.0
# The files a release's folder holds, each beside its detached signature.
MANIFEST, MANIFEST_SIGNATURE = "manifest.json", "manifest.json.sig"
# A SHA-256 as the manifest and the list give one; a platform as the manifest names one (linux-x64, linux-arm64,
# darwin-arm64, linux-x64-musl): the name a binary's path is made of, so letters, digits and inner dashes alone.
DIGEST = re.compile(r"[0-9a-f]{64}")
PLATFORM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# One line of a checksum list, its carriage return taken off: the digest, a space, a space or a * (binary), the name.
SUMS_LINE = re.compile(rb"([0-9a-f]{64}) [ *](.+)")
# The image's file name: one name, no / and no control character, only ever compared.
FILE_NAME = re.compile(r"[^/\x00-\x1f\x7f]+")
# A document's URL as it may be given: visible ASCII, one word (urlsplit would take a tab or a newline out unsaid).
URL = re.compile(r"[!-~]{1,4096}")
# A key's fingerprint as a person gives one beside the key's text: 40 hex digits, spaces between them allowed, as gpg
# prints one.
FINGERPRINT = re.compile(r"[0-9A-Fa-f]{40}")
# How much of what a server named is shown, made plain, before it is quoted as JSON.
SHOWN = 100


class DownloadError(Exception):
    """A vendor's document refused, in this module's words: what was asked for, and why."""


class NotARelease(DownloadError):
    """The version asked for, or the one a channel named, is no version of SIGNED_SINCE or later: Anthropic signed no
    manifest of it. A channel's is refused before its manifest is asked for, a version asked for before anything is."""


class NotFetched(DownloadError):
    """A document the transport could not fetch: the document and its URL said, its query left out, then the
    transport's own words. `error` is the transport's error (Unreachable, Untrusted among them, or TimedOut), for a
    caller that tells a certificate's refusal from a connection's."""

    def __init__(self, said: str, error: TransportError | None = None) -> None:
        super().__init__(said)
        self.error = error


class NotServed(DownloadError):
    """A document was answered with another status than 200: a 3xx among them, which is never followed."""


class Oversized(DownloadError):
    """A document is longer than its cap, and was read no further."""


class Unverified(DownloadError):
    """A document's detached signature does not verify by its key, the vendored one or a person's own: openpgp.verify's
    reason is said."""


class NotThisRelease(DownloadError):
    """Anthropic's signed manifest is not the release it was fetched as: no JSON object, another version, or no binary
    with a SHA-256."""


class NotListed(DownloadError):
    """A checksum list names the image not once: no line, or more than one, names it."""


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


def _get(transport: Transport, url: str, path: str, what: str, most: int, deadline: float) -> bytes:
    """The body of path on transport, url as it is said, whose answer must be a 200 of at most most bytes: the status
    is read first, so that an error page longer than a channel's pointer is said as the status it came with. A request
    the transport could not make is NotFetched, naming the document, raised once the handler that caught the transport's
    error has ended, so that it carries no cause."""
    answer: Answer | None = None
    failed: TransportError | None = None
    try:
        answer = transport.request("GET", path, deadline=deadline)
    except TooLarge:
        pass  # past the transport's cap, DOCUMENT_MOST, and past most with it: said below, outside the handler
    except TransportError as error:
        failed = error
    if failed is not None:
        raise NotFetched(f"the {what}, {url}, could not be fetched: {failed}", failed)
    if answer is not None and answer.status != 200:
        never = ", a redirect, which is never followed" if 300 <= answer.status < 400 else ""
        raise NotServed(f"{url} answered {answer.status}{never}, not the {what}")
    if answer is None or len(answer.body) > most:
        raise Oversized(f"{url} sent more than {most} bytes, which no {what} is")
    return answer.body


def _verify(data: bytes, signature: bytes, armored: str, fingerprint: str, what: str) -> None:
    """Return when signature verifies data by the key fingerprint names in armored; Unverified, with openpgp's reason,
    otherwise. Decided by whether openpgp.verify returned, never by the refusal's words: a refusal with none is a
    refusal."""
    verified, reason = False, ""
    try:
        openpgp.verify(data, signature, armored, fingerprint)
        verified = True
    except openpgp.Refused as refused:
        reason = str(refused)
    if not verified:
        raise Unverified(f"{what} does not verify by the key {fingerprint}: {reason or 'no reason given'}")


def _key(key: object) -> keys.Key:
    if not isinstance(key, keys.Key):
        raise TypeError("the key is a keys.Key: a vendored key file and its fingerprint")
    return key


def _given_key(key: object, fingerprint: object) -> tuple[str, str]:
    """The armored text and the fingerprint a list is verified by: a keys.Key's file and fingerprint, or the armored
    text a person gave with the fingerprint it is found by. Refused before anything is fetched: anything else, a text
    that is no armored key, a fingerprint that is not 40 hex digits, and one that names no primary key of the text (a
    subkey's, say), which openpgp would refuse only once both documents were fetched, as no key of the vendored ones."""
    if isinstance(key, keys.Key):
        if fingerprint is not None:
            raise TypeError("a keys.Key names its own fingerprint: none is given beside it")
        return key.armored(), key.fingerprint
    if not isinstance(key, str):
        raise TypeError("the key is a keys.Key, or the armored text of one with the fingerprint it is found by")
    try:
        found = openpgp.packets(openpgp.dearmor(key, "PUBLIC KEY BLOCK"))
    except openpgp.Refused:
        found = None
    if found is None:
        raise ValueError("the key given as text is not one ASCII-armored PGP public key block")
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch("".join(fingerprint.split())):
        raise ValueError("a key given as text comes with the fingerprint it is found by: 40 hex digits")
    wanted = "".join(fingerprint.split()).upper()
    # A primary key's packet (tag 6) of version 4, the one kind openpgp finds a key by.
    if not any(tag == 6 and body[:1] == b"\x04" and openpgp.fingerprint(body) == wanted for tag, body in found):
        raise ValueError(
            f"the key given as text holds no primary key whose fingerprint is {wanted}: give the fingerprint of its"
            " primary key, never a subkey's"
        )
    return key, wanted


def _split(url: object, what: str) -> tuple[str, str]:
    """A document's URL as the transport takes it: the https address of its host, and the path with its query, which a
    pre-signed URL's signature may be in. Refused before anything is fetched: a space or a control character, another
    scheme, a user or a password, and a fragment, which is never sent."""
    if not isinstance(url, str):
        raise TypeError(f"the {what}'s URL is a str")
    if not URL.fullmatch(url):
        raise ValueError(f"the {what}'s URL is one word of visible ASCII: no space, no control character")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        parts, port = None, None
    if parts is None or parts.scheme != "https" or not parts.hostname or port == 0:
        raise ValueError(f"the {what}'s URL is an https:// address, fetched over https only")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError(f"the {what}'s URL has no user, password or fragment")
    if parts.path.startswith("//"):
        # The transport takes a path's leading slashes off, so //a/b would be fetched as /a/b: another document.
        raise ValueError(f"the {what}'s URL has a path that begins with //, which would be fetched as another")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    origin = f"https://{host}" + (f":{port}" if port else "")
    return origin, parts.path + (f"?{parts.query}" if parts.query else "")


def _said_url(origin: str, path: str) -> str:
    """A document's URL as a message says it: its query left out, which may carry a pre-signed URL's signature."""
    plain, _, query = path.partition("?")
    return f"{origin}{plain}" + ("?…" if query else "")


def _file_name(file_name: object) -> bytes:
    if not isinstance(file_name, str) or not FILE_NAME.fullmatch(file_name) or file_name in (".", ".."):
        raise ValueError("the image's file name is one name, with no / and no control character")
    return file_name.encode("utf-8")


def _fetched(url: str, what: str, tls_ca_file: str | os.PathLike[str] | None, proxy: str | None) -> bytes:
    """The document at url, on a transport of its own URL's host, through proxy, the one chosen for url."""
    origin, path = _split(url, what)
    with Transport(
        origin,
        context=trust.context(tls_ca_file),
        user_agent=USER_AGENT,
        read=LIST_READ,
        most=DOCUMENT_MOST,
        proxy=proxy,
    ) as documents:
        return _get(documents, _said_url(origin, path), path, what, DOCUMENT_MOST, LIST_DEADLINE)


def _digest(listing: bytes, file_name: str, wanted: bytes, url: str, kind: str) -> str:
    """The digest of the one line of listing that names wanted, the line read as today's role reads it: NotListed where
    no line or more than one does."""
    digests = [
        found.group(1).decode("ascii")
        for line in listing.split(b"\n")
        if (found := SUMS_LINE.fullmatch(line.removesuffix(b"\r"))) and found.group(2) == wanted
    ]
    if len(digests) != 1:
        raise NotListed(f"the {kind} {url} names {_shown(file_name)} {len(digests)} times, not once")
    return digests[0]


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
    NotThisRelease, NotFetched with the transport's error, and trust.Unusable where the certificates cannot be used."""
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
            named = _get(documents, f"{base}/{wanted}", wanted, "channel's pointer", POINTER_MOST, CLAUDE_DEADLINE)
            version = named.decode("utf-8", "replace").strip()
            if not _signed(version):
                raise NotARelease(
                    f"{base}/{wanted} named {_shown(version)}, which is not a version of {SIGNED_SINCE} or later, the"
                    " first whose release Anthropic signs"
                )
        at = f"{version}/{MANIFEST}"
        manifest = _get(documents, f"{base}/{at}", at, "release's manifest", DOCUMENT_MOST, CLAUDE_DEADLINE)
        at = f"{version}/{MANIFEST_SIGNATURE}"
        signature = _get(documents, f"{base}/{at}", at, "manifest's signature", DOCUMENT_MOST, CLAUDE_DEADLINE)
    _verify(manifest, signature, key.armored(), key.fingerprint, f"{base}/{version}/{MANIFEST}")
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


def signed_digest(
    list_url: str,
    signature_url: str,
    file_name: str,
    *,
    key: keys.Key | str = keys.UBUNTU_CLOUD_IMAGE,
    fingerprint: str | None = None,
    tls_ca_file: str | os.PathLike[str] | None = None,
    list_proxy: str | None = None,
    signature_proxy: str | None = None,
) -> str:
    """The SHA-256 of file_name, 64 lower-case hex digits, as the checksum list at list_url gives it (today's
    proxmox_template_image_checksum, sha256:<its URL>), once its detached signature at signature_url (today's
    proxmox_template_image_signature) verifies by key: Ubuntu's cloud image key by default, a keys.Key, or the armored
    text of a person's own key (today's proxmox_template_image_key) with the fingerprint of a primary key in it. Each
    document is fetched over https from its own URL, on a transport of its URL's host: the two may be on two hosts, each
    reached through the proxy chosen for its own URL (proxy.choose's), list_proxy and signature_proxy, an http:// URL,
    or none. Connections verify against certifi's list and the file tls_ca_file names, where it names one. Raises
    ValueError or TypeError for a URL, a file name or a key that is not one, before anything is fetched; NotServed,
    Oversized, Unverified, NotListed, NotFetched with the transport's error, and trust.Unusable where the certificates
    cannot be used."""
    armored, found_by = _given_key(key, fingerprint)
    wanted = _file_name(file_name)
    origin, path = _split(list_url, "checksum list")
    _split(signature_url, "checksum list's signature")
    listing = _fetched(list_url, "checksum list", tls_ca_file, list_proxy)
    signature = _fetched(signature_url, "checksum list's signature", tls_ca_file, signature_proxy)
    url = _said_url(origin, path)
    _verify(listing, signature, armored, found_by, url)
    return _digest(listing, file_name, wanted, url, "signed checksum list")


def listed_digest(
    list_url: str,
    file_name: str,
    *,
    tls_ca_file: str | os.PathLike[str] | None = None,
    proxy: str | None = None,
) -> str:
    """The SHA-256 of file_name as the unsigned checksum list at list_url gives it - an image held to its checksum
    alone, today's proxmox_template_image_checksum as sha256:<the list's URL> with no signature set - read with the
    same line reader as a signed list, over https, on a transport of its URL's host, through proxy, the one chosen for
    list_url. Raises as signed_digest does, but Unverified: nothing signs it."""
    wanted = _file_name(file_name)
    origin, path = _split(list_url, "checksum list")
    listing = _fetched(list_url, "checksum list", tls_ca_file, proxy)
    return _digest(listing, file_name, wanted, _said_url(origin, path), "checksum list")
