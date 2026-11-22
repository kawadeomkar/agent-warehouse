"""The connection the Proxmox client sends its token on: verified against what the host keeps, before anything is
written (docs/architecture/engine.md §16.5).

A host keeps one of two things (init's to work out, card E3). An anchor file, its node's own certificate or its CA,
which is then the context's only source of roots: made by today's trust.anchored_context, the one way a context is
made from a certificate the host gave, strict and with a partial chain on every Python, and the name the API was
reached at checked. Or no file, where the host's certificate verifies against the computer's certificates, as the
vendors' do: certifi's list, with the file the setting tls_ca_file names added to it (vendors/trust.py). Where the
anchor is the node's own certificate, its SHA-256 fingerprint as it was recorded is held too: the transport hands this
module's check the certificate the server presented, after the handshake and before the first byte of a request, and
a certificate that is not the one recorded is refused there, nothing sent. Wherever a fingerprint is recorded, its name
is not checked, whatever the anchor, a CA's included: the fingerprint names the one certificate the host may present,
and the anchor still verifies it, so a host reached at an address its certificate does not name, a tailnet's or a
forwarded port's, is reached through its node's own certificate. Without one, an anchor and no anchor keep the name
checked, since each verifies any certificate it signed. Either way the certificate is always checked: there is no
context that checks nothing. No context logs its session's keys, whatever SSLKEYLOGFILE says and however Python was
started: the token's connection is the one the engine most needs kept.

A Pin's context, where a fingerprint is recorded, checks no name of itself: the fingerprint's check is transport()'s,
handed beside it. So nothing but this module reads a Pin's context (tests/unit/test_aw_vendor_trust.py holds that), and
a connection of a Pin's is made only by transport().

This module makes the client's transport, the one connection a Proxmox token goes on, so it alone hands the transport a
check of the peer; it touches neither http.client nor a socket. Of today's trust.py it binds anchored_context alone,
never the module, so that no module reaches the rest of today's trust through this one's names (a context of the
interpreter's own store with no name check among them). It reads no setting and no variable: the anchor's text,
the recorded fingerprint, the certificate file's setting and the proxy are its caller's.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import ssl
from collections.abc import Callable

from ..schema.patterns import full
from ..trust import anchored_context
from ..vendors import trust as vendors_trust
from ..vendors.transport import Transport

# What the client says it is, on every request.
USER_AGENT = "agent-warehouse"
# How long making a connection waits for each step, in seconds: the transport's own limit, named here so that a caller
# can bound a call by it (agent.py's polls).
CONNECT = 5.0


class Mismatch(Exception):
    """The certificate the server presented is not the one recorded for the host: its fingerprint differs."""


@dataclasses.dataclass(frozen=True)
class Pin:
    """What a host's connections are verified with: the context; whether it is the host's anchor alone, rather than
    the computer's certificate file; and, where the anchor is the node's own certificate, its SHA-256 fingerprint as
    recorded, as openssl prints one (colon-separated upper-case hex), else "". Its context is read by this module
    alone: with a fingerprint recorded it checks no name, and the fingerprint's check is transport()'s."""

    context: ssl.SSLContext
    anchored: bool
    fingerprint: str = ""


def context(anchor_pem: str | None, tls_ca_file: str | os.PathLike[str] | None = None) -> ssl.SSLContext:
    """The context the client's connections verify with, the name checked: the certificates of anchor_pem alone, the
    host's anchor file's text, where it keeps one; else the computer's certificates, certifi's list with the file
    tls_ca_file names added to it. It logs no session's keys: each kind is made by hand, so that neither opens the file
    SSLKEYLOGFILE names, as the standard library's ready-made context does unless Python ignores the environment, and
    its keylog_filename is set to none here too. Raises ValueError for an anchor that holds no certificate, and
    vendors.trust.Unusable for a file that cannot be used."""
    made = anchored_context(anchor_pem) if anchor_pem is not None else vendors_trust.context(tls_ca_file)
    made.keylog_filename = None
    return made


def _fingerprint(value: str) -> str:
    """A SHA-256 fingerprint as openssl prints one; ValueError for anything else."""
    if not isinstance(value, str) or not value or not full("fingerprint", value):
        raise ValueError("a certificate's fingerprint is its SHA-256 digest as openssl prints it, AA:BB:...")
    return value.upper()


def check(expected_fingerprint: str) -> Callable[[bytes], None]:
    """The check the transport calls with the certificate the server presented, as DER, before anything is written:
    it raises Mismatch where that certificate's SHA-256 fingerprint is not expected_fingerprint, the one recorded for
    the host's own certificate. Raises ValueError at once for a fingerprint that is not one."""
    expected = _fingerprint(expected_fingerprint)

    def held(presented: bytes) -> None:
        digest = hashlib.sha256(presented).hexdigest().upper()
        if ":".join(digest[at : at + 2] for at in range(0, len(digest), 2)) != expected:
            raise Mismatch("its SHA-256 fingerprint is not the one recorded for the host")

    return held


def kept(anchor_pem: str | None, fingerprint: str = "", tls_ca_file: str | os.PathLike[str] | None = None) -> Pin:
    """The Pin of what a host keeps: its anchor file's text, or None where it keeps none; the fingerprint recorded for
    the node's own certificate where that is the anchor; and the computer's setting tls_ca_file, read only where there
    is no anchor. Where a fingerprint is recorded, the context checks no name, whatever the anchor: the transport's
    check, which transport() hands beside it, holds the certificate presented to that fingerprint, which names the one
    certificate, and the anchor still verifies it. A fingerprint is held only beside an anchor: ValueError otherwise,
    or for one that is not one."""
    if fingerprint and anchor_pem is None:
        raise ValueError("a recorded fingerprint goes with the node's own certificate as the host's anchor")
    recorded = _fingerprint(fingerprint) if fingerprint else ""
    made = context(anchor_pem, tls_ca_file)
    if recorded:
        # The fingerprint, checked before a byte is written, says more than a name could: the certificate is that one.
        made.check_hostname = False
    return Pin(made, anchor_pem is not None, recorded)


def transport(url: str, pin: Pin, proxy: str | None = None, read: float = 30.0) -> Transport:
    """The transport to the API at url, every connection made with pin's context and, where a fingerprint is recorded,
    checked against it before anything is written; through proxy, an http:// URL, where there is one (proxy.choose
    gives it for the API's address)."""
    held = check(pin.fingerprint) if pin.fingerprint else None
    return Transport(
        url, context=pin.context, user_agent=USER_AGENT, connect=CONNECT, read=read, proxy=proxy, peer_check=held
    )
