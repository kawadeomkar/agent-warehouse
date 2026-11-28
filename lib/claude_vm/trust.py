"""The certificate a Proxmox host's API is checked against, as just init works it out (playbooks/init_check.yml,
docs/architecture/lifecycle.md §4.13): which of what this machine could trust makes the API's certificate verify, as
the run's own clients verify it, and whether what the address presents is the host's at all. Stdlib only, but for
certifi's bundle where it is there; the filters of filter_plugins/settings.py. No probe here sends the token.

No single file works for every host. On every Python the project runs on (3.13 or later) the standard library's client
context verifies strictly - ssl's VERIFY_X509_STRICT, which Ansible's uri and urllib3 from 2.4, so requests and every
community.proxmox module, use - and strict verification refuses Proxmox's own CA as every Proxmox VE 8 host has it,
and every 9 host's made before pve-cluster 9.1.2 and not issued again by 9.1.8: it has no keyUsage extension. That
context also sets VERIFY_X509_PARTIAL_CHAIN, so the node's own certificate in the trust file is an anchor of its own.
So each candidate is tried as the run's clients will try it - this machine's trust store, the host's CA, the node's
certificate - and the first that verifies is the one kept.
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import ssl
import urllib.parse

from .text import one_line

# Each candidate as a sentence names it.
_NAMED = {"store": "this machine's trust store", "ca": "its CA", "node": "the node's own certificate"}

# A certificate as PEM holds it, and no more of a file than the certificates in it: a key or anything else in the files
# read from the host is never kept, nor said.
_PEM = re.compile(r"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]{64,16384}-----END CERTIFICATE-----")
_MOST = 65536
_STRICT = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN


def pem_certificates(text) -> str:
    """The PEM certificates of ``text`` (a file read from the Proxmox host), one after the other, each ending in a
    newline; "" where there are none, or the text is more than a certificate file is."""
    text = str(text or "")
    if len(text) > _MOST:
        return ""
    return "".join(found.group(0).replace("\r\n", "\n") + "\n" for found in _PEM.finditer(text))


def pem_first(text) -> str:
    """The first PEM certificate of ``text``, "" for none: of a node's certificate file, the node's own, without the
    chain a custom certificate is served with - kept whole, each intermediate would be an anchor of its own."""
    found = _PEM.search(pem_certificates(text))
    return found.group(0) + "\n" if found else ""


def _ders(pem) -> list[bytes]:
    """Each certificate of ``pem`` as DER; those that are no certificate are left out. The armor alone says nothing:
    each is parsed as OpenSSL parses a CA it is handed."""
    ders = []
    for found in _PEM.finditer(pem_certificates(pem)):
        try:
            der = ssl.PEM_cert_to_DER_cert(found.group(0) + "\n")
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=der)
        except (ValueError, ssl.SSLError):
            continue
        ders.append(der)
    return ders


def _fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[at : at + 2] for at in range(0, len(digest), 2))


def pem_fingerprint(pem) -> str:
    """The SHA-256 fingerprint of the first certificate of ``pem``, as openssl x509 -fingerprint -sha256 says it:
    colon-separated uppercase hex. "" for none."""
    ders = _ders(pem)
    return _fingerprint(ders[0]) if ders else ""


def _store_files() -> list[str | None]:
    """What "this machine's trust store" is to the run's clients: the bundle REQUESTS_CA_BUNDLE names, which the
    inventory hands Ansible's uri too; else both the standard library's store, which uri uses, and certifi's, which
    requests - under every module of Proxmox's collection - uses. None is the standard library's."""
    bundle = os.environ.get("REQUESTS_CA_BUNDLE", "")
    if bundle:
        return [bundle]
    try:
        import certifi  # requests' own bundle, where requests is installed
    except ImportError:
        return [None]
    return [None, certifi.where()]


def _context(pem: str | None, cafile: str | None, check_hostname: bool = True) -> ssl.SSLContext:
    """A client context as the run's clients make one, against ``pem`` (PEM text) or ``cafile`` (None: the standard
    library's store): the standard library's ready-made one, its flags its own, strict and a partial chain."""
    context = ssl.create_default_context(cadata=pem) if pem else ssl.create_default_context(cafile=cafile)
    if not check_hostname:
        context.check_hostname = False
    return context


def anchored_context(pem) -> ssl.SSLContext:
    """A client context that verifies the API's certificate against the certificates of ``pem`` alone - the anchor a
    host keeps, its node's own certificate or its CA - and checks the name it was reached at, with the flags the
    standard library's ready-made context has, which one made by hand lacks: strict, and a partial chain, so that the
    node's own certificate is an anchor of its own. For the Proxmox client of the engine that replaces the playbooks
    at 3.0 (docs/architecture/custom-code.md §7.16), and made by hand, not by _context: the standard library's
    ready-made context opens the file SSLKEYLOGFILE names, as it is made, unless Python ignores the environment, and
    that client logs no session's keys. Raises ValueError where ``pem`` holds no certificate."""
    ders = _ders(pem)
    if not ders:
        raise ValueError("the anchor holds no certificate")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.verify_flags |= _STRICT
    context.load_verify_locations(cadata="".join(ssl.DER_cert_to_PEM_cert(der) for der in ders))
    return context


def _hello(url: str, context: ssl.SSLContext, timeout: float, http: bool = True) -> bytes:
    """The certificate the API presents, as DER, once the handshake ``context`` makes succeeds; then, with ``http``,
    HEAD /version with no token, which Proxmox answers 501 at once, so that the answer is known to be HTTP - a GET with
    no token waits three seconds for its 401. Raises what the handshake raised."""
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname or "", parts.port or 443
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            presented = tls.getpeercert(binary_form=True) or b""
            if not http:
                return presented
            path = parts.path.rstrip("/") + "/version"
            tls.sendall(f"HEAD {path} HTTP/1.1\r\nHost: {parts.netloc}\r\nConnection: close\r\n\r\n".encode())
            if not tls.recv(64).startswith(b"HTTP/"):
                raise OSError("it answered something that is no HTTP")
            return presented


def _probe(
    url: str,
    pem: str | None,
    timeout: float,
    check_hostname: bool = True,
    http: bool = True,
) -> str:
    """ "" when the API's certificate verifies against ``pem`` (this machine's trust store, as every client of the run
    has it, for None) and, with ``http``, it answers HTTP; else why not, in the SSL library's own words."""
    for cafile in [None] if pem else _store_files():
        try:
            _hello(url, _context(pem, cafile, check_hostname), timeout, http)
        except ssl.SSLCertVerificationError as error:
            return str(error.verify_message or error)
        except (OSError, ssl.SSLError, ValueError) as error:
            return str(error) or type(error).__name__
    return ""


def proxmox_trust_pick(candidates, url, timeout=10) -> dict:
    """Which of ``candidates`` - ({"name", "pem"}, the pem None for this machine's trust store) - makes the API at
    ``url`` verify: {"name", "pem", "fingerprint"} of the first that does, "" for each where none does, with
    "refusals": [{"name", "why"}] of those tried before it. Of the node's certificate file only the first certificate
    is tried and kept."""
    refusals = []
    for candidate in candidates or []:
        pem = candidate.get("pem")
        if pem is not None:
            pem = pem_first(pem) if candidate.get("name") == "node" else pem_certificates(pem)
            if pem == "":
                continue
        why = _probe(str(url), pem, float(timeout))
        if not why:
            return {
                "name": candidate.get("name", ""),
                "pem": pem or "",
                "fingerprint": pem_fingerprint(pem) if pem else "",
                "refusals": refusals,
            }
        refusals.append({"name": candidate.get("name", ""), "why": why})
    return {"name": "", "pem": "", "fingerprint": "", "refusals": refusals}


def proxmox_trust_holds(pem, url, timeout=10) -> dict:
    """Whether the API at ``url`` verifies against what checking uses now: ``pem``, the trust file's text, or this
    machine's trust store for "" - {"verified", "why", "fingerprint"}, the last the kept file's own."""
    pem = pem_certificates(pem) if pem else None
    if pem == "":
        return {"verified": False, "why": "the trust file holds no certificate", "fingerprint": ""}
    why = _probe(str(url), pem, float(timeout))
    return {"verified": not why, "why": why, "fingerprint": pem_fingerprint(pem) if pem else ""}


def _presented(url: str, timeout: float) -> bytes:
    """The certificate the API at ``url`` presents, as DER, read with a context that checks nothing, so that a
    certificate no candidate verifies can still be named, and with nothing sent after the handshake. Raises what the
    handshake raised."""
    reading = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    reading.check_hostname = False
    reading.verify_mode = ssl.CERT_NONE
    return _hello(url, reading, timeout, http=False)


def proxmox_trust_shown(url, timeout=10) -> dict:
    """What the API at ``url`` presents, read with no check and no token, whole: {"pem", "fingerprint", "why"}, the
    certificate as PEM and its SHA-256 fingerprint, or "" for each and why nothing was read. For the init of the engine
    that replaces the playbooks at 3.0 (docs/architecture/engine.md §16.4), which keeps it as the host's anchor only
    where a person vouched for it by its fingerprint, or root SSH showed it to be the host's own; nothing of today's
    product calls it."""
    try:
        presented = _presented(str(url), float(timeout))
    except (OSError, ssl.SSLError, ValueError) as error:
        return {"pem": "", "fingerprint": "", "why": str(error) or type(error).__name__}
    if not presented:
        return {"pem": "", "fingerprint": "", "why": "it presented no certificate"}
    return {"pem": ssl.DER_cert_to_PEM_cert(presented), "fingerprint": _fingerprint(presented), "why": ""}


def proxmox_trust_presented(candidates, url, timeout=10) -> dict:
    """What the API at ``url`` presents, read with no check and no token, and whether it is the host's own, as the
    host's files read over root SSH say (``candidates``, the store's left out: a certificate any public CA signed proves
    no host): the node's certificate itself, or one that chains to the host's CA or its node's certificate whatever
    name it holds. {"fingerprint", "host", "why"}: a certificate that is the host's under another name is a name to fix;
    one that is not is something else answering at the address. The certificate alone decides, never what follows the
    handshake: what answers sees which request carries no token, and may say nothing to it."""
    try:
        presented = _presented(str(url), float(timeout))
    except (OSError, ssl.SSLError, ValueError) as error:
        return {"fingerprint": "", "host": False, "why": str(error) or type(error).__name__}
    if not presented:
        return {"fingerprint": "", "host": False, "why": "it presented no certificate"}
    host = False
    for candidate in candidates or []:
        pem = pem_certificates(candidate.get("pem")) if candidate.get("name") != "store" else ""
        if not pem:
            continue
        if presented in _ders(pem) or not _probe(str(url), pem, float(timeout), check_hostname=False, http=False):
            host = True
            break
    return {"fingerprint": _fingerprint(presented), "host": host, "why": ""}


def proxmox_trust_said(picked, limit=400) -> str:
    """Why each candidate ``proxmox_trust_pick`` tried before the one it kept did not verify, as one line of plain text
    of at most ``limit`` characters: "this machine's trust store: <why>; its CA: <why>". The SSL library's own words,
    which quote the certificate the host presented."""
    refusals = picked.get("refusals") if isinstance(picked, dict) else None
    said = [
        f"{_NAMED.get(str(one.get('name')), str(one.get('name')))}: {one.get('why', '')}"
        for one in refusals or []
        if isinstance(one, dict)
    ]
    return one_line("; ".join(said), limit)
