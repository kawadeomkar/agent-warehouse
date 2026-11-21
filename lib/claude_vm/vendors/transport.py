"""One HTTPS transport for every request the new engine sends (docs/architecture/engine.md §16.7).

Over `http.client` and `ssl` alone, so it follows no redirect, reads no certificate variable, no netrc and no proxy
variable, by construction: a 3xx is an answer, the context its connections verify with is the caller's, and the proxy
is the caller's too, an `http://` URL or none (`proxy.choose` works out today's). Through a proxy a connection is a
CONNECT tunnel, asked for and read here: TLS and the caller's check of the peer's certificate are end to end with the
server, so a token never leaves the tunnel's TLS. `peer_check` is handed the peer's certificate, as DER, after each new
connection's handshake and before anything is written on it; its raising ends the call as `PeerRefused`, with nothing
sent. A certificate that does not verify against the context is `Untrusted`, an `Unreachable` of its own, whose
sentence says how to add a root with the setting tls_ca_file only where OpenSSL found no issuer it trusts
(UNKNOWN_ISSUER).

Bounded: an answer's body is held to `most` bytes; every wait to `connect` or `read` seconds; and a whole call, its
tries and their pauses included, to its deadline, which every step of making a connection (each of the name's
addresses in turn, a proxy's CONNECT and its answer, held to TUNNEL_MOST bytes, the TLS handshake) and every read and
every send of a connection is held to. Only the name's lookup is not: the system's resolver bounds it. http.client is
handed the connection made, and never makes one. A GET or HEAD is tried again, at most ATTEMPTS times in all, on a
connection that could not be made or was dropped, or on 502, 503 or 504; any other method is sent once, and a failure
once it has left is `Unknown`, never a second send. A kept connection whose far end has closed it is put aside before
a request is written on it, which costs no try.

A header's value may be a `Secret`, or a tuple or list of `str` and `Secret` parts; a form's value may be a `Secret`,
or a list or tuple of values that may be; a `Secret` anywhere else in a form, or anywhere in a query, is refused, never
sent as its mark. A proxy's user and password are a `Secret` from the moment the URL is read. A Secret is revealed in
`_plain` alone, for `_put`, which puts a header, `_Form`, which encodes a form, and `_connect_head`, which writes a
proxy's credentials into the CONNECT, and the plain text is never held in a name of the frames an error is raised
through. No error of this module says a header's value or a peer's words: a request http.client would refuse to make
is said as NOT_MADE, since http.client's own words quote the header; what http.client raised of an answer is said by
what it means, since its words quote the answer, which may echo a header; of a proxy's answer only its status is said;
and every error is raised outside the handler that caught its cause, so it carries no cause either. A socket made for
a connection that is not kept is closed however the making ends, a Ctrl-C included.

Blocking, and safe to call from several threads, with one connection a thread; a caller on an asyncio loop uses
asyncio.to_thread. It builds no context and reads no setting and no variable.
"""

from __future__ import annotations

import base64
import dataclasses
import http.client
import io
import json as json_text
import re
import select
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any, BinaryIO

from ..secret import Secret
from ..text import one_line
from .trust import SETTING

# A GET or HEAD is tried this many times in all, pausing so long before each try after the first; any other method
# once. On these answers, and on a connection not made or dropped (N-14's proposal, not measured).
ATTEMPTS = 3
PAUSES = (0.5, 1.0)
RETRIED = frozenset({"GET", "HEAD"})
AGAIN = frozenset({502, 503, 504})
# A call's deadline, in seconds, where its caller names none; and an answer's body at most, in bytes, by default.
DEADLINE = 120.0
MOST = 16 * 1024 * 1024
# How much is read or sent in one wait.
CHUNK = 64 * 1024
# A proxy's answer to a CONNECT, its status line and headers, at most this many bytes.
TUNNEL_MOST = 64 * 1024
# What a request http.client would refuse to make is said as: its own words quote the header.
NOT_MADE = "the request could not be made"

# A header's name, as HTTP has one; a value, visible ASCII, a space or a tab, and nothing that ends a line.
_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_VALUE = re.compile(r"[\t\x20-\x7e]*")
# A method, and a path, visible ASCII with no space.
_METHOD = re.compile(r"[A-Z]{1,20}")
_PATH = re.compile(r"[!-~]*")
# A host's name or address, as a URL gives it: visible ASCII, which a CONNECT line and a Host header carry as it is.
_HOST = re.compile(r"[!-~]+")
# A proxy's status line, as http.client reads one: a version, a status of three digits, and a reason or none.
_STATUS = re.compile(rb"HTTP/[0-9.]+[ \t]+([0-9]{3})(?:[ \t\r\n]|\Z)")
# What the transport says of every request itself: the caller names none of these, but the length and the type of a
# stream's body.
_OWN = frozenset({"host", "user-agent", "transfer-encoding", "content-length", "content-type"})
_STREAM_OWN = frozenset({"host", "user-agent", "transfer-encoding"})
_FORM = "application/x-www-form-urlencoded"
_JSON = "application/json"


class TransportError(Exception):
    """One request's failure in the transport's own words, which never say a header's value."""


class Unreachable(TransportError):
    """No answer could be had: the connection was not made, or, for a GET or HEAD, it was dropped on every try. For
    any other method nothing was sent."""


class TimedOut(TransportError):
    """A wait ran past its limit, or the call past its deadline, with nothing in doubt: before anything was sent, or
    for a GET or HEAD."""


class TooLarge(TransportError):
    """The answer's body is longer than `most` bytes, and was read no further."""


class Unknown(TransportError):
    """A request other than a GET or HEAD left and no answer came: it may or may not have been carried out. It is
    never sent again; its caller reads the state to know."""


class PeerRefused(TransportError):
    """The caller's check of the peer's certificate refused it; nothing was sent on that connection."""


class NotMade(TransportError):
    """A request http.client would refuse to make, a header's, a path's or a method's: said as NOT_MADE."""


# OpenSSL's verify codes for a certificate whose issuer it does not know: the one failure that a root of the person's
# own, added with the setting tls_ca_file, mends. Never a wrong name, an expired certificate or any other.
UNKNOWN_ISSUER = frozenset(
    {
        2,  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT
        18,  # X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT
        19,  # X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN
        20,  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
        21,  # X509_V_ERR_UNABLE_TO_VERIFY_LEAF_SIGNATURE
    }
)


class Untrusted(Unreachable):
    """The server's certificate does not verify against the context's roots: no connection was made, nothing was sent,
    and it is not tried again. reason is what OpenSSL said of the certificate, and code its verify code; where the code
    says the issuer is unknown (UNKNOWN_ISSUER), the sentence says how a root of the person's own is added, with the
    setting tls_ca_file, and otherwise gives the reason alone. A copy or a pickle is made again from the two, so it
    says the sentence once."""

    def __init__(self, reason: str, code: int = 0) -> None:
        self.reason = reason
        self.code = code
        said = f"its certificate does not verify ({reason})"
        if code in UNKNOWN_ISSUER:
            said += (
                ": where a root of your own signed it, such as your company's, add that root to the file of PEM"
                f" certificates the setting {SETTING} names, which is added to certifi's list"
            )
        super().__init__(said)

    def __reduce__(self) -> tuple[type[Untrusted], tuple[str, int]]:
        return type(self), (self.reason, self.code)


@dataclasses.dataclass(frozen=True)
class Answer:
    """What the server answered: its status, a 3xx included, its headers as it sent them, its body, and the reason its
    status line gave, as it sent it - the peer's words, which its caller makes plain before it says any of them."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    reason: str = ""

    def header(self, name: str) -> str | None:
        """The first value of the header name, in any case; None where there is none."""
        wanted = name.lower()
        return next((value for key, value in self.headers if key.lower() == wanted), None)


class _Again(Exception):
    """A GET's or HEAD's failure that a further try may mend: a connection not made, or dropped."""

    def __init__(self, error: TransportError) -> None:
        super().__init__()
        self.error = error


class _Late(TimeoutError):
    """The call's deadline has passed."""


class _NoTunnel(Exception):
    """The proxy gave no tunnel: what it answered, in this module's words."""


class _Clock:
    """One call's deadline, and how long the next wait may be."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.at = time.monotonic() + seconds

    def left(self) -> float:
        return self.at - time.monotonic()

    def wait(self, limit: float) -> float:
        """limit, or what is left of the deadline where that is less; _Late once nothing is."""
        left = self.left()
        if left <= 0:
            raise _Late
        return min(limit, left)


class _Wire:
    """A connection's TLS socket as http.client is handed it once the connection is made: every send and every read
    waits at most the read limit, and never past the call's deadline."""

    def __init__(self, sock: ssl.SSLSocket, read: float, clock: _Clock) -> None:
        self.sock = sock
        self.read = read
        self.clock = clock
        # An answer http.client is still reading keeps the socket open after the connection lets it go, as a socket's
        # own makefile does: its Connection: close hands the socket to the answer.
        self.readers = 0
        self.closed = False

    def _wait(self) -> None:
        self.sock.settimeout(self.clock.wait(self.read))

    def sendall(self, data: Any) -> None:
        view = memoryview(data).cast("B")
        for start in range(0, len(view), CHUNK):
            self._wait()
            self.sock.sendall(view[start : start + CHUNK])

    def recv_into(self, buffer: Any) -> int:
        self._wait()
        return self.sock.recv_into(buffer)

    def makefile(self, mode: str = "rb") -> io.BufferedReader:
        self.readers += 1
        return io.BufferedReader(_Reader(self), CHUNK)

    def fileno(self) -> int:
        return self.sock.fileno()

    def close(self) -> None:
        self.closed = True
        if self.readers <= 0:
            self.sock.close()

    def released(self) -> None:
        self.readers -= 1
        if self.closed and self.readers <= 0:
            self.sock.close()


class _Reader(io.RawIOBase):
    """An answer's bytes, read from a _Wire: closing it leaves the connection open for the next request."""

    def __init__(self, wire: _Wire) -> None:
        self.wire = wire

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        return self.wire.recv_into(buffer)

    def close(self) -> None:
        if not self.closed:
            super().close()
            self.wire.released()


@dataclasses.dataclass(frozen=True)
class _Proxy:
    """A proxy's host and port, and the Basic credentials of its CONNECT: the user and password as base64, a Secret,
    or None where the URL names no user."""

    host: str
    port: int
    credentials: Secret | None = dataclasses.field(default=None, repr=False)


def _basic(user: str, password: str) -> Secret | None:
    """The Basic credentials of user and password, their %-escapes undone, as base64 and a Secret; None where they
    cannot be encoded, with nothing raised that could hold them."""
    try:
        return Secret(
            base64.b64encode(f"{urllib.parse.unquote(user)}:{urllib.parse.unquote(password)}".encode()).decode()
        )
    except UnicodeError:
        return None


def _proxy(url: str | None) -> _Proxy | None:
    """The proxy url names: an http:// URL, its port 80 where it names none, and its user and password, where it has
    them, held as a Secret and sent to the proxy alone as Basic credentials of the CONNECT. Any other scheme is refused
    by name, socks5 as requests refuses it with no SOCKS package; and no message quotes the URL, which may hold a
    password."""
    if url is None:
        return None
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme != "http":
        named = one_line(scheme, 20) or "none"
        raise ValueError(f"a proxy of the scheme {named} is not one this program takes: only an http:// proxy")
    try:
        port = parts.port or 80
    except ValueError:
        port = 0
    if not parts.hostname or not _HOST.fullmatch(parts.hostname) or not port:
        raise ValueError("the proxy is not an http://host:port URL")
    credentials = None
    if parts.username is not None:
        credentials = _basic(parts.username, parts.password or "")
        if credentials is None:
            raise ValueError("the proxy's user and password cannot be sent as Basic credentials")
    return _Proxy(parts.hostname, port, credentials)


def _plain(value: str | Secret) -> str:
    """value as it is sent: a Secret's own value, read here and nowhere else, or the str. Never raises; its three
    callers, _put, _Form and _connect_head, hold what it returns only in frames of their own that have ended before any
    error is raised."""
    return value.reveal() if isinstance(value, Secret) else value


def _connect_head(target: str, credentials: Secret | None) -> bytes:
    """The head of a CONNECT to target, as http.client wrote one (HTTP/1.1, a Host header), with the proxy's Basic
    credentials, read by _plain, where there are any. Handed on in one expression, so that the plain credentials are
    held in this frame alone, which has ended before the head is sent."""
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if credentials is not None:
        lines.append(f"Proxy-Authorization: Basic {_plain(credentials)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def _put(conn: http.client.HTTPSConnection | None, name: object, value: object) -> bool:
    """Put one header on conn's request, its value a str, a Secret, or a tuple or list of str and Secret parts joined,
    each Secret read by _plain; with conn None, only say whether it would. False, with nothing put, where the name or
    the value is not one to send as it is: a name that is no HTTP token, a value with anything but visible ASCII, spaces
    and tabs, or parts of another kind. Returns rather than raises, so that no traceback holds this frame and the plain
    value in it."""
    if isinstance(value, tuple | list):
        if not all(isinstance(part, str | Secret) for part in value):
            return False
        text: object = "".join(_plain(part) for part in value)
    else:
        text = _plain(value) if isinstance(value, Secret) else value
    if not isinstance(name, str) or not isinstance(text, str) or not _NAME.fullmatch(name):
        return False
    if not _VALUE.fullmatch(text):
        return False
    if conn is not None:
        conn.putheader(name, text)
    return True


def _close(*socks: socket.socket | None) -> None:
    """Close each socket made, leaving none to the collector."""
    for sock in socks:
        if sock is not None:
            sock.close()


def _closed_by_peer(wire: _Wire) -> bool:
    """Whether a kept connection's far end has closed it, or sent what nobody asked for: readable while idle."""
    try:
        poller = select.poll()
        poller.register(wire.fileno(), select.POLLIN)
        return bool(poller.poll(0))
    except (OSError, ValueError):
        return True


def _holds_a_secret(value: Any, seen: set[int] | None = None) -> bool:
    """Whether value is a Secret or holds one at any depth: among a mapping's keys and values, or in any collection
    but a str or bytes, each walked once. An iterator is not walked, which would use it up: urlencode says it by its
    str, never a Secret in it."""
    if isinstance(value, Secret):
        return True
    if isinstance(value, str | bytes | bytearray) or not isinstance(value, Collection):
        return False
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    items = [one for pair in value.items() for one in pair] if isinstance(value, Mapping) else value
    return any(_holds_a_secret(one, seen) for one in items)


def _pairs(values: Any) -> list[tuple[Any, Any]]:
    """A query's or a form's pairs, read once: a mapping's items, or the pairs given."""
    return list(values.items() if isinstance(values, Mapping) else values)


def _no_secret(query: list[tuple[Any, Any]]) -> None:
    """Refuses a Secret anywhere in a query, a name or a value at any depth, which would be sent in the request's line,
    and as its mark: a secret goes in a header or a form."""
    for name, value in query:
        if _holds_a_secret(name) or _holds_a_secret(value):
            raise TypeError("a Secret goes in a header or a form, never in a query")


def _sent_whole(value: Any) -> bool:
    """Whether a form's value holds a Secret only where it is sent whole: as the value itself, or as one of a list or
    tuple of values; never as a name, nor deeper, where urlencode would send its mark."""
    if isinstance(value, Secret):
        return True
    if isinstance(value, list | tuple):
        return not any(_holds_a_secret(one) for one in value if not isinstance(one, Secret))
    return not _holds_a_secret(value)


class _Form:
    """A form's body whose values may be Secrets, encoded only where it is measured or sent: the request and its try
    hand it on in one expression each, and never hold the plain body in a name of their own."""

    def __init__(self, form: Any) -> None:
        self.pairs = _pairs(form)
        for name, value in self.pairs:
            if _holds_a_secret(name) or not _sent_whole(value):
                raise TypeError("a Secret in a form is one of its values, or one in a list or tuple of them")

    def encoded(self) -> bytes | None:
        """The body as it is sent, each Secret read by _plain; None where it cannot be encoded, with nothing raised
        that could hold a value."""
        try:
            return urllib.parse.urlencode(
                [
                    (key, [_plain(v) if isinstance(v, Secret) else v for v in value])
                    if isinstance(value, list | tuple)
                    else (key, _plain(value) if isinstance(value, Secret) else value)
                    for key, value in self.pairs
                ],
                doseq=True,
            ).encode("ascii")
        except (TypeError, ValueError):
            return None

    def size(self) -> int | None:
        """The body's length in bytes; None where it cannot be encoded."""
        encoded = self.encoded()
        return None if encoded is None else len(encoded)


def _seconds(value: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise ValueError(f"{what} is a number of seconds above 0")
    return float(value)


class Transport:
    """HTTPS requests to one server, by paths under base_url's, each connection made with context and checked by
    peer_check, through proxy where there is one: see the module's docstring for the rules."""

    def __init__(
        self,
        base_url: str,
        context: ssl.SSLContext,
        user_agent: str,
        connect: float = 5.0,
        read: float = 30.0,
        most: int = MOST,
        proxy: str | None = None,
        peer_check: Callable[[bytes], None] | None = None,
    ) -> None:
        parts = urllib.parse.urlsplit(base_url)
        try:
            port = parts.port or 443
        except ValueError:
            port = 0
        if parts.scheme != "https" or not parts.hostname or not _HOST.fullmatch(parts.hostname) or not port:
            raise ValueError("the transport takes an https:// address only")
        if parts.username is not None or parts.query or parts.fragment or not _PATH.fullmatch(parts.path):
            raise ValueError("the transport's address is a host, a port and a path, with no user, query or fragment")
        if not isinstance(context, ssl.SSLContext):
            raise TypeError("the transport is given the ssl.SSLContext its connections verify with")
        if not isinstance(user_agent, str) or not user_agent or not _VALUE.fullmatch(user_agent):
            raise ValueError("the user agent is one line of visible ASCII")
        if isinstance(most, bool) or not isinstance(most, int) or most < 0:
            raise ValueError("most is a number of bytes")
        if peer_check is not None and not callable(peer_check):
            raise TypeError("peer_check is a function of the peer's certificate, or None")
        self._host, self._port, self._base = parts.hostname, port, parts.path.rstrip("/")
        self._context = context
        self._user_agent = user_agent
        self._connect = _seconds(connect, "connect")
        self._read = _seconds(read, "read")
        self._most = most
        self._proxy = _proxy(proxy)
        self._peer_check = peer_check
        self._local = threading.local()
        self._held: set[http.client.HTTPSConnection] = set()
        self._lock = threading.Lock()

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close every connection kept, each thread's: for when no request is in flight."""
        with self._lock:
            held = list(self._held)
        for conn in held:
            self._forget(conn)

    def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str | Secret | tuple[str | Secret, ...] | list[str | Secret]] | None = None,
        query: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        form: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        json: Any = None,
        stream: BinaryIO | None = None,
        deadline: float | None = None,
    ) -> Answer:
        """Send one request to path under the base URL's, with headers (a value may be a Secret, or a tuple or list
        of str and Secret parts), query, and at most one body - a form, encoded, whose values may be Secrets; JSON; or
        a stream, a binary file read in chunks, sent chunked unless headers name its Content-Length - and return the
        Answer, a 3xx included. deadline bounds the whole call, making its connections included, in seconds (DEADLINE
        where None). Raises a TransportError: Unreachable (Untrusted among them), TimedOut, TooLarge, Unknown,
        PeerRefused or NotMade."""
        clock = _Clock(DEADLINE if deadline is None else _seconds(deadline, "deadline"))
        headers = dict(headers or {})
        if sum(body is not None for body in (form, json, stream)) > 1:
            raise ValueError("a request has one body at most: a form, JSON or a stream")
        if query is not None:
            query = _pairs(query)
            _no_secret(query)
        named = {str(name).lower() for name in headers}
        if named & (_STREAM_OWN if stream is not None else _OWN):
            raise ValueError("the transport names a request's host, user agent, length and type itself")
        if not isinstance(method, str) or not _METHOD.fullmatch(method) or not isinstance(path, str):
            raise NotMade(NOT_MADE)
        target = f"{self._base}/{path.lstrip('/')}"
        if query is not None:
            target += "?" + urllib.parse.urlencode(query, doseq=True)
        if not _PATH.fullmatch(target):
            raise NotMade(NOT_MADE)
        lines: list[tuple[str, Any]] = [("User-Agent", self._user_agent)]
        body: bytes | _Form | BinaryIO | None = None
        chunked = False
        if form is not None:
            body = _Form(form)
            size = body.size()
            if size is None:
                raise NotMade(NOT_MADE)
            lines += [("Content-Type", _FORM), ("Content-Length", str(size))]
        elif json is not None:
            body = json_text.dumps(json, separators=(",", ":")).encode()
            lines += [("Content-Type", _JSON), ("Content-Length", str(len(body)))]
        elif stream is not None:
            body, chunked = stream, "content-length" not in named
            if chunked:
                lines.append(("Transfer-Encoding", "chunked"))
        elif method not in RETRIED:
            lines.append(("Content-Length", "0"))
        lines += headers.items()
        # Every header held to what http.client sends as it is before a byte of the request is made: nothing of a
        # header refused here is ever in a connection's buffer.
        if not all(_put(None, name, value) for name, value in lines):
            raise NotMade(NOT_MADE)
        tries = ATTEMPTS if method in RETRIED else 1
        last: TransportError | Answer | None = None
        for attempt in range(tries):
            if attempt:
                pause = PAUSES[min(attempt, len(PAUSES)) - 1]
                if clock.left() <= pause:
                    break
                time.sleep(pause)
            try:
                answer = self._once(method, target, lines, body, chunked, clock)
            except _Again as again:
                last = again.error
                continue
            if method in RETRIED and answer.status in AGAIN:
                last = answer
                continue
            return answer
        if isinstance(last, Answer):
            return last
        assert last is not None
        raise last

    # ------------------------------------------------------------------------------------------------ one try ----

    def _once(
        self,
        method: str,
        target: str,
        lines: list[tuple[str, Any]],
        body: bytes | _Form | BinaryIO | None,
        chunked: bool,
        clock: _Clock,
    ) -> Answer:
        """One try: the request written on this thread's connection, and its answer read whole. Every error is raised
        after the handler that caught its cause has ended. A form is encoded in the call that sends it, so that no
        name here holds it plain."""
        conn = self._connection(clock)
        failure: TransportError | _Again | None = None
        sent = False
        try:
            conn.putrequest(method, target, skip_accept_encoding=True)
            for name, value in lines:
                _put(conn, name, value)
            sent = True
            conn.endheaders(body.encoded() if isinstance(body, _Form) else body, encode_chunked=chunked)
            response = conn.getresponse()
            headers = tuple(response.getheaders())
            answer = Answer(response.status, headers, self._body(response, method), response.reason)
        except TooLarge as error:
            failure = error
        except TimeoutError:
            failure = (
                self._late(clock) if (not sent or method in RETRIED) else self._lost(method, self._no_answer(clock))
            )
        except ValueError:
            failure = self._lost(method, "it answered something that is no HTTP") if sent else NotMade(NOT_MADE)
        except (OSError, http.client.HTTPException) as error:
            why = self._why(error, "the connection was dropped")
            failure = self._lost(method, why) if sent else _Again(Unreachable(why))
        except BaseException:
            # A Ctrl-C, or anything else: the connection may be part way through a request, so it is kept no longer.
            self._forget(conn)
            raise
        if failure is not None:
            self._forget(conn)
            raise failure
        if conn.sock is None:
            self._forget(conn)
        return answer

    def _body(self, response: http.client.HTTPResponse, method: str) -> bytes:
        """The answer's body, at most `most` bytes: TooLarge past that, read no further."""
        if method == "HEAD":
            response.read()
            return b""
        if response.length is not None and response.length > self._most:
            raise TooLarge(f"the answer ({response.status}) is longer than {self._most} bytes")
        chunks, size = [], 0
        while chunk := response.read(min(CHUNK, self._most + 1 - size)):
            size += len(chunk)
            if size > self._most:
                raise TooLarge(f"the answer ({response.status}) is longer than {self._most} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def _lost(self, method: str, why: str) -> TransportError | _Again:
        """A connection dropped once the request was written: a GET or HEAD may try again; any other is Unknown."""
        if method in RETRIED:
            return _Again(Unreachable(why))
        return Unknown(f"{why}: the request may or may not have been carried out")

    def _late(self, clock: _Clock) -> TimedOut:
        return TimedOut(self._no_answer(clock))

    def _no_answer(self, clock: _Clock) -> str:
        if clock.left() <= 0:
            return f"no answer within the call's deadline of {clock.seconds:g} s"
        return f"no answer within {self._read:g} s"

    @staticmethod
    def _why(error: BaseException, default: str) -> str:
        """What error means: for what http.client raised of an answer, this module's words, never http.client's, which
        quote the answer's status line or version, and so a header the peer echoed there; else default and the system's
        words."""
        if isinstance(error, http.client.IncompleteRead):
            return "the answer was cut short"
        if isinstance(error, http.client.HTTPException) and not isinstance(error, http.client.RemoteDisconnected):
            return "it answered something that is no HTTP"
        said = getattr(error, "strerror", None) or str(error)
        return f"{default}: {one_line(said, 160)}" if said else default

    # -------------------------------------------------------------------------------------------- connections ----

    def _connection(self, clock: _Clock) -> http.client.HTTPSConnection:
        """This thread's kept connection, where its far end has not closed it; else a new one, its peer checked."""
        conn = getattr(self._local, "conn", None)
        if conn is not None and isinstance(conn.sock, _Wire) and not _closed_by_peer(conn.sock):
            conn.sock.clock = clock
            return conn
        if conn is not None:
            self._forget(conn)
        conn = self._new(clock)
        self._local.conn = conn
        with self._lock:
            self._held.add(conn)
        return conn

    def _new(self, clock: _Clock) -> http.client.HTTPSConnection:
        """A connection made and its peer's certificate checked, before anything is written on it, each step under the
        call's clock: the TCP connection, to the server or to the proxy and then its CONNECT tunnel, and the TLS
        handshake with the server. http.client is handed the TLS socket, and never makes a connection itself. A socket
        made for a connection that is not kept is closed however the making ends, a Ctrl-C included."""
        failure: TransportError | _Again | None = None
        raw: socket.socket | None = None
        tls: ssl.SSLSocket | None = None
        kept = False
        try:
            try:
                if self._proxy is None:
                    raw = self._dial(self._host, self._port, clock)
                else:
                    raw = self._dial(self._proxy.host, self._proxy.port, clock)
                    self._tunnel(raw, clock)
                raw.settimeout(clock.wait(self._connect))
                tls = self._context.wrap_socket(raw, server_hostname=self._host)
            except _Late:
                failure = TimedOut(f"no connection within the call's deadline of {clock.seconds:g} s")
            except _NoTunnel as error:
                failure = _Again(Unreachable(f"the connection could not be made: {error}"))
            except ssl.SSLCertVerificationError as error:
                failure = Untrusted(one_line(str(error.verify_message or error), 160), error.verify_code or 0)
            except TimeoutError:
                failure = (
                    TimedOut(f"no connection within the call's deadline of {clock.seconds:g} s")
                    if clock.left() <= 0
                    else _Again(Unreachable(f"no connection within {self._connect:g} s"))
                )
            except ValueError:
                failure = NotMade(NOT_MADE)
            except OSError as error:
                failure = _Again(Unreachable(self._why(error, "the connection could not be made")))
            if failure is None and tls is not None:
                refused = self._refused(tls)
                if refused is None:
                    conn = http.client.HTTPSConnection(self._host, self._port, context=self._context, blocksize=CHUNK)
                    conn.auto_open = 0
                    conn.sock = _Wire(tls, self._read, clock)
                    kept = True
                    return conn
                failure = PeerRefused(f"the peer's certificate was refused: {refused}")
        finally:
            if not kept:
                _close(tls, raw)
        raise failure or Unreachable("the connection could not be made")

    def _dial(self, host: str, port: int, clock: _Clock) -> socket.socket:
        """A TCP connection to host's port: each of the name's addresses tried in turn, as socket.create_connection
        tries them, an address whose family the system cannot open passed over like one that does not answer, but each
        for at most `connect` seconds and none past the call's deadline. The lookup itself is bounded by the system's
        resolver alone. Raises what the first address failed with, or _Late; a socket made is closed however the try
        ends."""
        first: OSError | None = None
        for family, kind, proto, _, address in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
            sock: socket.socket | None = None
            try:
                sock = socket.socket(family, kind, proto)
                sock.settimeout(clock.wait(self._connect))
                sock.connect(address)
            except _Late:
                _close(sock)
                raise
            except OSError as error:
                _close(sock)
                first = first or error
                continue
            except BaseException:
                _close(sock)
                raise
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass  # as http.client: a system with no TCP_NODELAY sends as it may
            return sock
        raise first or OSError("the name has no address")

    def _tunnel(self, sock: socket.socket, clock: _Clock) -> None:
        """Ask the proxy on sock for a tunnel to the server, as http.client's CONNECT asks, and read the head of its
        answer: every send and read held to `connect` seconds and the call's deadline, and the head to TUNNEL_MOST
        bytes, read a byte at a time so that nothing of the server's is taken. _NoTunnel for anything but a 200, said
        by its status alone; what the proxy wrote is never said."""
        assert self._proxy is not None
        name = f"[{self._host}]" if ":" in self._host else self._host
        target = f"{name}:{self._port}"
        sock.settimeout(clock.wait(self._connect))
        sock.sendall(_connect_head(target, self._proxy.credentials))
        head = bytearray()
        while not head.endswith((b"\n\r\n", b"\n\n")):
            if len(head) >= TUNNEL_MOST:
                raise _NoTunnel(f"the proxy's answer to the tunnel is longer than {TUNNEL_MOST} bytes")
            sock.settimeout(clock.wait(self._connect))
            byte = sock.recv(1)
            if not byte:
                raise _NoTunnel("the proxy closed the connection")
            head += byte
        status = _STATUS.match(head)
        if status is None:
            raise _NoTunnel("the proxy answered something that is no HTTP")
        if status.group(1) != b"200":
            raise _NoTunnel(f"the proxy answered {status.group(1).decode()} to the tunnel")

    def _refused(self, sock: ssl.SSLSocket) -> str | None:
        """None where peer_check, if any, takes the peer's certificate; else what it said."""
        if self._peer_check is None:
            return None
        try:
            self._peer_check(sock.getpeercert(binary_form=True) or b"")
        except Exception as error:  # any refusal of the caller's check is a refusal
            return one_line(str(error), 160) or type(error).__name__
        return None

    def _forget(self, conn: http.client.HTTPSConnection) -> None:
        """Close conn and keep it no longer: its socket at once, though an answer was being read from it."""
        wire = conn.sock
        conn.close()
        if isinstance(wire, _Wire):
            wire.sock.close()
        with self._lock:
            self._held.discard(conn)
        if getattr(self._local, "conn", None) is conn:
            self._local.conn = None
