"""One HTTPS transport for every request of the new engine, and the proxy it is handed (lib/claude_vm/vendors/
transport.py and proxy.py; docs/architecture/engine.md §16.7).

Every request goes to TLS servers this test starts in its own process, on loopback: a ThreadingHTTPServer wrapped in
TLS with the CA and the node's certificate tests/unit/pki.py makes, as tests/unit/test_proxmox_trust.py's _serve does,
whose handlers answer, redirect, drop a connection, drip or wait, and keep every request they were asked; and a
stand-in CONNECT proxy, on loopback too, which keeps what it was asked and every byte it carried, or answers the CONNECT
itself, dripping. The transport follows no redirect, reads no certificate variable, netrc or proxy variable, takes a
proxy only as an argument, sends a write once, bounds every body and wait and the whole call, making its connection
included, and never says a header's value or a peer's words. proxy.choose works out today's proxy from the environment
it is given, as requests does. The rules at the end hold the new engine's modules to one transport.
"""

from __future__ import annotations

import ast
import base64
import copy
import dataclasses
import errno
import http.server
import io
import logging
import os
import pickle
import re
import socket
import ssl
import sys
import threading
import time
import traceback
import types
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
from harness import REPO, repo_files
from pki import proxmox_pki
from test_aw_layout import NEW

from claude_vm.secret import Secret
from claude_vm.vendors import proxy, transport
from claude_vm.vendors.transport import (
    NOT_MADE,
    UNKNOWN_ISSUER,
    Answer,
    NotMade,
    PeerRefused,
    TimedOut,
    TooLarge,
    Transport,
    Unknown,
    Unreachable,
    Untrusted,
)

# The module's two certificate authorities: the server's, and one that signed nothing the server shows. Cheap enough
# to make again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "other_pki"}
AGENT = "agent-warehouse-test"
# A token's secret no output may show: low in entropy, so that gitleaks takes it for no key.
CANARY = "unit-test-transport-canary-value"
TRANSPORT = REPO / "lib" / "claude_vm" / "vendors" / "transport.py"
PROXY = REPO / "lib" / "claude_vm" / "vendors" / "proxy.py"
# What proxy.py may call of urllib.request: the readers that take what they read as an argument or read the system's
# own settings. Never getproxies or proxy_bypass themselves, which read os.environ, which choose never reads; never an
# opener.
URLLIB_READERS = frozenset({"proxy_bypass_environment", "getproxies_macosx_sysconf", "proxy_bypass_macosx_sysconf"})
# The modules of the new engine that hand the transport a check of the peer's certificate, each with why.
PEER_CHECKS: dict[str, str] = {
    "lib/claude_vm/pve/pin.py": "the Proxmox client's pin: where the host's anchor is its node's own certificate,"
    " pin.check refuses one whose SHA-256 fingerprint is not the one recorded, before the token is written",
}
CERTIFICATE_VARIABLES = re.compile(r"\b(SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE|NETRC)\b")
PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
# proxy.py's own readers of the system's settings, before the fixture below stands in for them.
SYSTEM_PROXIES, SYSTEM_BYPASS = proxy._system_proxies, proxy._system_bypass


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(scope="module")
def other_pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("other-pki"), key_usage=True)


@pytest.fixture(autouse=True)
def system(monkeypatch):
    """This machine's own proxy settings stood in for: none, unless a test gives some. The developer's Mac may have a
    proxy of its own."""
    settings = {"proxies": {}, "bypass": False}
    monkeypatch.setattr(proxy, "_system_proxies", lambda: dict(settings["proxies"]))
    monkeypatch.setattr(proxy, "_system_bypass", lambda host: settings["bypass"])
    return settings


@pytest.fixture
def fast(monkeypatch):
    """The pauses between a GET's tries made short."""
    monkeypatch.setattr(transport, "PAUSES", (0.01, 0.01))


def _context(pem: str) -> ssl.SSLContext:
    """A context that verifies against pem alone, as the engine's callers make theirs."""
    return ssl.create_default_context(cadata=pem)


# ---------------------------------------------------------------- the server ----


@dataclasses.dataclass
class Asked:
    """One request a server was asked: its method, path, headers by their lower-case names, body and the client's
    port, one for each connection."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    port: int


Behave = Callable[[http.server.BaseHTTPRequestHandler, Asked, int], None]


class _Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        pass  # a client that went first, as a test makes it: nothing to print


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _any(self) -> None:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = self._chunked()
        else:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        asked = Asked(
            self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body, self.client_address[1]
        )
        with self.server.lock:
            self.server.asked.append(asked)
            nth = sum(1 for one in self.server.asked if one.method == self.command)
        try:
            self.server.behave(self, asked, nth)
        except OSError:
            self.close_connection = True  # the client went first: what it does then is the test's

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _any

    def _chunked(self) -> bytes:
        body = b""
        while size := int(self.rfile.readline().strip(), 16):
            body += self.rfile.read(size)
            self.rfile.readline()
        self.rfile.readline()
        return body

    def log_message(self, *args) -> None:
        pass


def answer(status: int = 200, body: bytes = b"ok", headers=(), close_after: bool = False) -> Behave:
    """Answer with status, headers and body; with close_after, then close the connection without a word of it, as a
    server whose keep-alive ran out does."""

    def behave(handler, asked, nth):
        handler.send_response(status)
        for name, value in headers:
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if asked.method != "HEAD":
            handler.wfile.write(body)
        handler.close_connection = close_after

    return behave


def drop(handler, asked, nth) -> None:
    """Close the connection once the request is read, with no answer."""
    handler.close_connection = True


@pytest.fixture
def serve(pki):
    """start(behave) starts a TLS server on loopback with the node's certificate: .url, and .asked, every request."""
    servers = []

    def start(behave: Behave | None = None) -> http.server.ThreadingHTTPServer:
        server = _Server(("127.0.0.1", 0), _Handler)
        server.asked, server.lock, server.behave = [], threading.Lock(), behave or answer()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(pki["cert"], pki["key"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.url = f"https://127.0.0.1:{server.server_address[1]}/api2/json"
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _client(server, pki, **kwargs) -> Transport:
    kwargs.setdefault("context", _context(pki["ca"]))
    return Transport(server.url, user_agent=AGENT, **kwargs)


# ---------------------------------------------------------------- the stand-in proxy ----


class _Tunnel:
    """A CONNECT proxy on loopback: each request asked of it, every byte it carried from a client on, and how many
    connections it took. Given an answer, it answers every CONNECT with it and closes; with drip, it answers each a byte
    every DRIP seconds, a 200's status line and then header lines without end, for as long as the client waits."""

    DRIP = 0.05

    def __init__(self, answer: bytes | None = None, drip: bool = False) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.answer, self.drip = answer, drip
        self.asked: list[bytes] = []
        self.carried = bytearray()
        self.connections = 0
        self.lock = threading.Lock()
        self.sockets: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            with self.lock:
                self.connections += 1
                self.sockets.append(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            data = client.recv(4096)
            if not data:
                return
            head += data
        request, _, rest = head.partition(b"\r\n\r\n")
        with self.lock:
            self.asked.append(request)
        words = request.split(b"\r\n", 1)[0].split()
        if len(words) != 3 or words[0] != b"CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return
        if self.answer is not None:
            client.sendall(self.answer)
            client.close()
            return
        if self.drip:
            try:
                for byte in b"HTTP/1.1 200 Connection established\r\n" + b"X-Drip: y\r\n" * 100_000:
                    client.sendall(bytes([byte]))
                    time.sleep(self.DRIP)
            except OSError:
                pass  # the client went, as it should
            return
        host, _, port = words[1].decode().rpartition(":")
        upstream = socket.create_connection((host, int(port)))
        with self.lock:
            self.sockets.append(upstream)
            self.carried.extend(rest)
        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        upstream.sendall(rest)
        threading.Thread(target=self._pump, args=(upstream, client, False), daemon=True).start()
        self._pump(client, upstream, True)

    def _pump(self, source: socket.socket, sink: socket.socket, keep: bool) -> None:
        try:
            while data := source.recv(65536):
                if keep:
                    with self.lock:
                        self.carried.extend(data)
                sink.sendall(data)
        except OSError:
            pass
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self) -> None:
        try:
            self.listener.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.listener.close()
        # Shut down before closing: a socket closed while a pump's recv waits on it sends no FIN on Linux, and the
        # pump's own shutdown then finds it closed, so a server still in its TLS handshake (an Untrusted client's)
        # would wait on the upstream connection for good, and its fixture's shutdown() with it.
        with self.lock:
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()


@pytest.fixture
def tunnel():
    stand_in = _Tunnel()
    yield stand_in
    stand_in.close()


@pytest.fixture
def proxy_that():
    """proxy_that(answer=..., drip=...) starts a stand-in proxy that answers every CONNECT itself."""
    made = []

    def start(**kwargs) -> _Tunnel:
        made.append(_Tunnel(**kwargs))
        return made[-1]

    yield start
    for stand_in in made:
        stand_in.close()


# ---------------------------------------------------------------- a request and its answer ----


@pytest.mark.parametrize(
    ("given", "body", "content_type"),
    [
        (lambda: {"form": {"name": "alpha", "cores": 4}}, b"name=alpha&cores=4", "application/x-www-form-urlencoded"),
        (lambda: {"json": {"name": "alpha", "cores": 4}}, b'{"name":"alpha","cores":4}', "application/json"),
        (lambda: {"stream": io.BytesIO(b"x" * 200_000)}, b"x" * 200_000, None),
        (
            lambda: {"stream": io.BytesIO(b"abc"), "headers": {"Content-Length": "3", "Content-Type": "a/b"}},
            b"abc",
            "a/b",
        ),
    ],
    ids=["form", "json", "stream chunked", "stream with its length"],
)
def test_a_request_reaches_the_server_with_its_headers_and_its_body(serve, pki, given, body, content_type) -> None:
    server = serve(answer(201, b'{"data": 1}', headers=[("X-Seen", "yes")]))
    given = given()
    headers = {"Authorization": Secret(f"PVEAPIToken=engine@pve!aw={CANARY}"), **given.pop("headers", {})}
    with _client(server, pki) as client:
        got = client.request("POST", "/nodes/pve/storage", headers=headers, query={"content": "iso"}, **given)
    assert isinstance(got, Answer) and (got.status, got.body) == (201, b'{"data": 1}')
    assert got.header("x-seen") == "yes" and got.header("X-Absent") is None
    [asked] = server.asked
    assert (asked.method, asked.path, asked.body) == ("POST", "/api2/json/nodes/pve/storage?content=iso", body)
    # A Secret's value is sent as it is: revealed, in the one place.
    assert asked.headers["authorization"] == f"PVEAPIToken=engine@pve!aw={CANARY}"
    assert asked.headers["user-agent"] == AGENT and asked.headers["host"] == server.url.split("/")[2]
    assert asked.headers.get("content-type") == content_type
    if "stream" in given and "content-length" not in asked.headers:
        assert asked.headers["transfer-encoding"] == "chunked"


def test_a_get_with_no_body_sends_no_length_and_a_post_says_its_length_is_none(serve, pki) -> None:
    server = serve()
    with _client(server, pki) as client:
        assert client.request("GET", "version").status == 200
        assert client.request("POST", "nodes/pve/status/start").status == 200
        head = client.request("HEAD", "version")
    assert (head.status, head.body, head.header("Content-Length")) == (200, b"", "2")
    get, post, _ = server.asked
    assert "content-length" not in get.headers and post.headers["content-length"] == "0"


def test_an_answer_carries_the_reason_its_status_line_gave(serve, pki) -> None:
    server = serve(answer(404, b""))
    with _client(server, pki) as client:
        got = client.request("GET", "version")
    assert (got.status, got.reason) == (404, "Not Found")


def test_a_redirect_is_an_answer_and_the_second_address_is_never_asked(serve, pki) -> None:
    elsewhere = serve()
    server = serve(answer(302, b"", headers=[("Location", f"{elsewhere.url}/version")]))
    with _client(server, pki) as client:
        got = client.request("GET", "version", headers={"Authorization": Secret(CANARY)})
    assert got.status == 302 and got.header("Location") == f"{elsewhere.url}/version"
    assert len(server.asked) == 1 and elsewhere.asked == []


def test_the_certificate_variables_and_netrc_change_nothing(serve, pki, other_pki, tmp_path, monkeypatch) -> None:
    """The context is the caller's alone: with every variable a client library reads for its certificates pointing at
    the server's CA, a context that does not trust it still refuses the server; and pointing at another CA, a context
    that trusts the server still reaches it. netrc's credentials for the address are never sent."""
    server = serve()
    ca, other = tmp_path / "ca.pem", tmp_path / "other.pem"
    ca.write_text(pki["ca"])
    other.write_text(other_pki["ca"])
    netrc = tmp_path / "netrc"
    netrc.write_text(f"machine 127.0.0.1 login trap password {CANARY}\n")
    netrc.chmod(0o600)

    def trap(pem: Path) -> None:
        for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            monkeypatch.setenv(name, str(pem))
        monkeypatch.setenv("SSL_CERT_DIR", str(pem.parent))
        monkeypatch.setenv("NETRC", str(netrc))

    trap(ca)
    with _client(server, pki, context=_context(other_pki["ca"])) as client, pytest.raises(Untrusted) as refused:
        client.request("GET", "version")
    assert "certificate does not verify" in str(refused.value) and server.asked == []
    trap(other)
    with _client(server, pki) as client:
        assert client.request("GET", "version").status == 200
    [asked] = server.asked
    assert "authorization" not in asked.headers


def test_with_no_proxy_given_every_proxy_variable_is_ignored(serve, pki, tunnel, monkeypatch) -> None:
    server = serve()
    for name in PROXY_VARIABLES:
        monkeypatch.setenv(name, tunnel.url)
    with _client(server, pki) as client:
        assert client.request("GET", "version").status == 200
    assert len(server.asked) == 1 and tunnel.connections == 0


def test_through_a_proxy_the_tunnel_carries_tls_end_to_end(serve, pki, tunnel) -> None:
    server = serve()
    seen = []
    with _client(server, pki, proxy=tunnel.url, peer_check=seen.append) as client:
        got = client.request("GET", "version", headers={"Authorization": Secret(CANARY)})
        assert client.request("GET", "nodes").status == 200
    assert got.status == 200
    port = server.server_address[1]
    [asked] = tunnel.asked
    assert asked.split(b"\r\n")[0] == f"CONNECT 127.0.0.1:{port} HTTP/1.1".encode()
    assert b"proxy-authorization" not in asked.lower()
    # Nothing in clear: a TLS handshake record first, and neither the token nor the request's words.
    assert tunnel.carried[:1] == b"\x16"
    assert CANARY.encode() not in tunnel.carried and b"version" not in tunnel.carried and b"GET" not in tunnel.carried
    assert seen == [ssl.PEM_cert_to_DER_cert(pki["node"])]
    assert [one.path for one in server.asked] == ["/api2/json/version", "/api2/json/nodes"]
    assert server.asked[0].headers["authorization"] == CANARY


def test_a_proxy_s_credentials_go_to_the_proxy_alone(serve, pki, tunnel) -> None:
    server = serve()
    named = tunnel.url.replace("http://", "http://aw%20user:pa%3Ass@")
    with _client(server, pki, proxy=named) as client:
        assert client.request("GET", "version").status == 200
    credentials = base64.b64encode(b"aw user:pa:ss").decode()
    assert f"Proxy-Authorization: Basic {credentials}".encode() in tunnel.asked[0]
    assert "proxy-authorization" not in server.asked[0].headers


def _canary_as_basic() -> str:
    """The Basic credentials of the user aw and the canary as its password, as a CONNECT carries them."""
    return base64.b64encode(f"aw:{CANARY}".encode()).decode()


def test_the_proxy_s_password_is_a_secret_wherever_the_transport_holds_it(pki) -> None:
    held = transport._proxy(f"http://aw:{CANARY}@127.0.0.1:3128")
    assert held is not None and isinstance(held.credentials, Secret)
    with Transport(URL, _context(pki["ca"]), AGENT, proxy=f"http://aw:{CANARY}@127.0.0.1:3128") as client:
        shown = [repr(held), str(held), repr(vars(client)), repr(client)]
    assert not [text for text in shown if CANARY in text or _canary_as_basic() in text]


class _Interrupted:
    """A connection to a proxy that takes the CONNECT and is interrupted, as by a Ctrl-C, while its answer is awaited:
    what was sent on it, and whether it was closed."""

    def __init__(self) -> None:
        self.sent = b""
        self.closed = False

    def settimeout(self, seconds: float) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent += bytes(data)

    def recv(self, size: int) -> bytes:
        raise KeyboardInterrupt

    def close(self) -> None:
        self.closed = True


def test_a_ctrl_c_during_the_connect_closes_its_socket_and_no_frame_holds_the_password(
    pki, monkeypatch, caplog, capsys
) -> None:
    """The credentials go whole to the proxy, revealed only in the call that writes the CONNECT's head, which has
    ended by the time the answer is read: no frame the interrupt passes through holds them, and the socket is closed,
    never left to the collector."""
    caplog.set_level(logging.DEBUG)
    interrupted = _Interrupted()
    monkeypatch.setattr(Transport, "_dial", lambda self, host, port, clock: interrupted)
    with Transport(URL, _context(pki["ca"]), AGENT, proxy=f"http://aw:{CANARY}@127.0.0.1:3128") as client:
        with pytest.raises(KeyboardInterrupt) as stopped:
            client.request("GET", "version")
    assert interrupted.closed
    assert f"Proxy-Authorization: Basic {_canary_as_basic()}\r\n".encode() in interrupted.sent
    assert not [text for text in _said(stopped.value, caplog, capsys) if CANARY in text or _canary_as_basic() in text]


@pytest.mark.parametrize("url", ["socks5://127.0.0.1:1080", f"socks5h://user:{CANARY}@127.0.0.1:1080", "https://p:443"])
def test_a_proxy_of_another_scheme_is_refused_by_name(pki, url) -> None:
    with pytest.raises(ValueError) as refused:
        Transport("https://127.0.0.1:8006/api2/json", _context(pki["ca"]), AGENT, proxy=url)
    assert url.split(":")[0] in str(refused.value) and CANARY not in str(refused.value)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8006/", "ftp://127.0.0.1/", "127.0.0.1:8006", "https://"])
def test_an_address_that_is_not_https_is_refused(pki, url) -> None:
    with pytest.raises(ValueError):
        Transport(url, _context(pki["ca"]), AGENT)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_certificate_that_does_not_verify_is_untrusted_an_unreachable_that_names_the_setting(
    serve, pki, other_pki, tunnel, fast, method
) -> None:
    """Untrusted is an Unreachable, so a caller of the transport that catches the one catches the other; it is not
    tried again, even for a GET (one connection through the proxy); and its sentence says what OpenSSL found and how a
    root of the person's own is named."""
    server = serve()
    context = _context(other_pki["ca"])
    with _client(server, pki, context=context, proxy=tunnel.url) as client, pytest.raises(Untrusted) as untrusted:
        client.request(method, "version", headers={"Authorization": Secret(CANARY)})
    assert isinstance(untrusted.value, Unreachable) and untrusted.value.reason
    assert untrusted.value.code in UNKNOWN_ISSUER
    said = str(untrusted.value)
    assert said.startswith(f"its certificate does not verify ({untrusted.value.reason}): ")
    assert "tls_ca_file" in said and "PEM" in said and "certifi's list" in said and CANARY not in said
    assert tunnel.connections == 1 and server.asked == []


@pytest.mark.parametrize("code", [20, 64], ids=["an unknown issuer", "a wrong address"])
def test_untrusted_survives_a_copy_and_a_pickle_with_its_sentence_once(code) -> None:
    made = Untrusted("unable to get local issuer certificate", code)
    assert str(made).count("does not verify") == 1
    for again in (copy.copy(made), copy.deepcopy(made), pickle.loads(pickle.dumps(made))):
        assert type(again) is Untrusted and str(again) == str(made) and again.args == made.args
        assert (again.reason, again.code) == (made.reason, made.code)


def test_a_peer_check_that_raises_sends_nothing(serve, pki) -> None:
    server = serve()
    seen = []

    def refuse(der: bytes) -> None:
        seen.append(der)
        raise ValueError("not the recorded fingerprint")

    with _client(server, pki, peer_check=refuse) as client, pytest.raises(PeerRefused) as refused:
        client.request("GET", "version", headers={"Authorization": Secret(CANARY)})
    assert "not the recorded fingerprint" in str(refused.value)
    assert seen == [ssl.PEM_cert_to_DER_cert(pki["node"])] and server.asked == []


# ---------------------------------------------------------------- sent once, tried again ----


def test_a_post_whose_connection_drops_is_unknown_and_the_server_saw_it_once(serve, pki, fast) -> None:
    server = serve(drop)
    with _client(server, pki) as client, pytest.raises(Unknown) as unknown:
        client.request("POST", "nodes/pve/clone", form={"name": "alpha"})
    assert "may or may not have been carried out" in str(unknown.value)
    assert [one.method for one in server.asked] == ["POST"]


def test_a_get_is_asked_three_times_and_no_more(serve, pki, fast) -> None:
    server = serve(answer(503, b"busy"))
    with _client(server, pki) as client:
        got = client.request("GET", "pools")
    assert got.status == 503 and got.body == b"busy"
    assert [one.method for one in server.asked] == ["GET"] * transport.ATTEMPTS == ["GET"] * 3


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_get_answered_again_is_tried_again(serve, pki, fast, status) -> None:
    server = serve(lambda handler, asked, nth: answer(status if nth == 1 else 200)(handler, asked, nth))
    with _client(server, pki) as client:
        assert client.request("GET", "version").status == 200
    assert len(server.asked) == 2


def test_a_post_answered_503_is_not_sent_again(serve, pki, fast) -> None:
    server = serve(answer(503))
    with _client(server, pki) as client:
        assert client.request("POST", "pools").status == 503
    assert len(server.asked) == 1


def test_a_get_dropped_once_is_asked_again_on_a_new_connection(serve, pki, fast) -> None:
    server = serve(lambda handler, asked, nth: drop(handler, asked, nth) if nth == 1 else answer()(handler, asked, nth))
    with _client(server, pki) as client:
        assert client.request("GET", "version").body == b"ok"
    first, second = server.asked
    assert first.port != second.port


def test_the_pauses_between_tries_end_at_the_deadline(serve, pki) -> None:
    server = serve(answer(503))
    with _client(server, pki) as client:
        started = time.monotonic()
        assert client.request("GET", "version", deadline=0.3).status == 503
    assert len(server.asked) == 1 and time.monotonic() - started < transport.PAUSES[0]


def test_a_kept_connection_the_server_closed_is_put_aside_before_a_post(serve, pki) -> None:
    server = serve(answer(close_after=True))
    with _client(server, pki) as client:
        assert client.request("GET", "version").status == 200
        time.sleep(0.3)
        assert client.request("POST", "pools", form={"poolid": "aw"}).status == 200
    get, post = server.asked
    assert post.method == "POST" and get.port != post.port


def test_a_connection_nothing_answers_is_unreachable(pki, fast) -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        for method in ("GET", "POST"):
            with Transport(f"https://127.0.0.1:{port}", _context(pki["ca"]), AGENT) as client:
                with pytest.raises(Unreachable) as unreachable:
                    client.request(method, "version")
            assert "could not be made" in str(unreachable.value) and not isinstance(unreachable.value, Untrusted)


def test_an_address_whose_family_the_system_cannot_open_is_passed_over_for_the_next(serve, pki, monkeypatch) -> None:
    """As socket.create_connection passes over it: the name's first address is IPv6, which the system refuses to open
    (as a kernel booted with IPv6 off refuses it), and its second the server's. A POST, so that no second try hides a
    first that failed."""
    server = serve()
    port = server.server_address[1]
    addresses = [
        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", port, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
    ]
    opened = []

    def open_one(family=socket.AF_INET, kind=socket.SOCK_STREAM, proto=0):
        opened.append(family)
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, os.strerror(errno.EAFNOSUPPORT))
        return socket.socket(family, kind, proto)

    stand_in = {**vars(socket), "socket": open_one, "getaddrinfo": lambda *args, **kwargs: addresses}
    monkeypatch.setattr(transport, "socket", types.SimpleNamespace(**stand_in))
    with _client(server, pki) as client:
        assert client.request("POST", "pools", form={"poolid": "aw"}).status == 200
    assert opened == [socket.AF_INET6, socket.AF_INET] and [one.method for one in server.asked] == ["POST"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_tunnel_the_proxy_refuses_says_its_status_alone(serve, pki, proxy_that, fast, method) -> None:
    """A proxy's words are never said, only its status: its reason here is a canary. A GET asks for a tunnel again, at
    most three times in all; any other method once, with nothing sent."""
    server = serve()
    refusing = proxy_that(answer=f"HTTP/1.1 407 {CANARY}\r\nProxy-Authenticate: Basic {CANARY}\r\n\r\n".encode())
    with _client(server, pki, proxy=refusing.url) as client, pytest.raises(Unreachable) as refused:
        client.request(method, "version")
    assert str(refused.value) == "the connection could not be made: the proxy answered 407 to the tunnel"
    assert len(refusing.asked) == (3 if method == "GET" else 1) and server.asked == []


def test_a_proxy_that_answers_no_http_is_said_so(serve, pki, proxy_that) -> None:
    server = serve()
    garbled = proxy_that(answer=f"{CANARY} 200\r\n\r\n".encode())
    with _client(server, pki, proxy=garbled.url) as client, pytest.raises(Unreachable) as refused:
        client.request("POST", "version")
    assert str(refused.value) == "the connection could not be made: the proxy answered something that is no HTTP"


def test_each_thread_has_a_connection_of_its_own_and_keeps_it(serve, pki) -> None:
    server = serve(lambda handler, asked, nth: answer(body=asked.path.encode())(handler, asked, nth))
    answers: dict[int, list[bytes]] = {}
    with _client(server, pki) as client:

        def work(n: int) -> None:
            answers[n] = [client.request("GET", f"thread/{n}/{k}").body for k in range(3)]

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert answers == {n: [f"/api2/json/thread/{n}/{k}".encode() for k in range(3)] for n in range(4)}
    assert len({one.port for one in server.asked}) == 4 and len(server.asked) == 12


# ---------------------------------------------------------------- bounded ----


@pytest.mark.parametrize("length", [True, False], ids=["with its length", "to the connection's end"])
def test_a_body_one_byte_past_the_cap_is_refused(serve, pki, length) -> None:
    def behave(handler, asked, nth):
        body = b"y" * (100 if nth == 1 else 101)
        handler.send_response(200)
        if length:
            handler.send_header("Content-Length", str(len(body)))
        else:
            handler.send_header("Connection", "close")
            handler.close_connection = True
        handler.end_headers()
        handler.wfile.write(body)

    server = serve(behave)
    with _client(server, pki, most=100) as client:
        assert client.request("GET", "version").body == b"y" * 100
        with pytest.raises(TooLarge):
            client.request("GET", "version")
    assert len(server.asked) == 2


def _after(seconds: float, then: Behave) -> Behave:
    def behave(handler, asked, nth):
        time.sleep(seconds)
        then(handler, asked, nth)

    return behave


def test_a_wait_ends_at_its_limit_a_get_s_timed_out_and_a_post_s_unknown(serve, pki, fast) -> None:
    server = serve(_after(1.5, answer()))
    with _client(server, pki, read=0.3) as client:
        started = time.monotonic()
        with pytest.raises(TimedOut):
            client.request("GET", "version")
        assert time.monotonic() - started < 1.2
        with pytest.raises(Unknown):
            client.request("POST", "pools")
    assert [one.method for one in server.asked] == ["GET", "POST"]


def test_the_deadline_bounds_the_whole_call_though_every_wait_is_short(serve, pki) -> None:
    def drip(handler, asked, nth):
        handler.send_response(200)
        handler.send_header("Content-Length", "100")
        handler.end_headers()
        for _ in range(100):
            handler.wfile.write(b"z")
            handler.wfile.flush()
            time.sleep(0.1)

    server = serve(drip)
    with _client(server, pki, read=5) as client:
        started = time.monotonic()
        with pytest.raises(TimedOut) as late:
            client.request("GET", "version", deadline=1.0)
        took = time.monotonic() - started
    assert 0.9 < took < 3 and "deadline of 1 s" in str(late.value)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_the_deadline_bounds_making_the_connection_through_a_proxy_that_drips_its_answer(
    pki, proxy_that, method
) -> None:
    """A proxy that answers the CONNECT a byte at a time, each well within the connect limit, a status line and then
    headers without end: http.client's own CONNECT read that for minutes. Nothing was sent, so a POST is TimedOut too,
    never Unknown."""
    dripping = proxy_that(drip=True)
    with Transport(URL, _context(pki["ca"]), AGENT, connect=0.5, read=0.5, proxy=dripping.url) as client:
        started = time.monotonic()
        with pytest.raises(TimedOut) as late:
            client.request(method, "version", deadline=1.0)
        took = time.monotonic() - started
    assert 0.9 < took < 3 and "deadline of 1 s" in str(late.value)
    assert len(dripping.asked) == 1 and dripping.asked[0].startswith(b"CONNECT pve.lan:8006 HTTP/1.1\r\n")


def test_each_address_of_a_name_is_tried_but_none_past_the_deadline(pki, monkeypatch) -> None:
    """socket.create_connection gives each of a name's addresses the whole connect limit, so six silent ones would hold
    a call six times that. Here the addresses are stood in for, and each connect waits out its limit."""
    limits: list[float] = []

    def silent(sock, address) -> None:
        limits.append(sock.gettimeout())
        time.sleep(sock.gettimeout())
        raise TimeoutError("timed out")

    six = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 8006))] * 6
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: six)
    monkeypatch.setattr(socket.socket, "connect", silent)
    with Transport(URL, _context(pki["ca"]), AGENT, connect=0.4) as client:
        started = time.monotonic()
        with pytest.raises(TimedOut) as late:
            client.request("POST", "pools", deadline=1.0)
        took = time.monotonic() - started
    assert took < 1.5 and "deadline of 1 s" in str(late.value)
    assert 2 <= len(limits) <= 3 and all(limit <= 0.4 for limit in limits), limits


# ---------------------------------------------------------------- a secret never said ----


def _said(error: BaseException, caplog, capsys, frames: bool = True) -> list[str]:
    """Everything that could show error: its words, its repr, its arguments, its traceback printed, the log and what was
    printed; and with frames, each of its frames' own names and what they hold one level down, where a value the caller
    gave as a Secret is never plain."""
    said = [str(error), repr(error), *map(repr, error.args), "".join(traceback.format_exception(error))]
    tb = error.__traceback__ if frames else None
    while tb is not None:
        for value in tb.tb_frame.f_locals.values():
            said.append(repr(value) if not isinstance(value, str | bytes) else str(value))
            if isinstance(value, list | tuple | set | dict):
                items = value.items() if isinstance(value, dict) else enumerate(value)
                said += [repr(item) for item in items]
        tb = tb.tb_next
    out, err = capsys.readouterr()
    return [*said, caplog.text, out, err]


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": Secret(f"{CANARY}\r\nX-Injected: 1")},
        {"Authorization": Secret(f"{CANARY}\n")},
        {"X-Note": f"{CANARY}\r\n folded"},
        {"Authorization": Secret(f"{CANARY}\x00")},
        {"Bad Name": CANARY},
        {"Authorization": Secret(f"{CANARY}é")},
        {"Authorization": ("PVEAPIToken=engine@pve!aw=", Secret(f"{CANARY}\r\nX-Injected: 1"))},
        {"Authorization": ("PVEAPIToken=", Secret(CANARY), 1)},
        {"Authorization": ["Bearer ", Secret(CANARY), b"x"]},
    ],
    ids=[
        "secret with CRLF",
        "secret with LF",
        "plain folded",
        "secret with NUL",
        "a name with a space",
        "not ASCII",
        "a secret part with CRLF",
        "a part that is a number",
        "a part that is bytes",
    ],
)
def test_a_header_http_client_would_refuse_fails_and_says_nothing_of_it(serve, pki, caplog, capsys, headers) -> None:
    caplog.set_level(logging.DEBUG)
    server = serve()
    with _client(server, pki) as client, pytest.raises(NotMade) as refused:
        client.request("GET", "version", headers={"Accept": "application/json", **headers})
    error = refused.value
    assert str(error) == NOT_MADE == "the request could not be made"
    assert error.__cause__ is None and error.__context__ is None
    secret = any(
        isinstance(value, Secret) or (isinstance(value, tuple | list) and any(isinstance(v, Secret) for v in value))
        for value in headers.values()
    )
    assert not [text for text in _said(error, caplog, capsys, frames=secret) if CANARY in text]
    assert server.asked == []


# The ways a secret goes with a request: a header's whole value, a part of one, a form's value, one of a form's list.
WITH_A_SECRET = {
    "a header": lambda: {"headers": {"Authorization": Secret(CANARY)}, "form": {"poolid": "aw"}},
    "a header's part": lambda: {"headers": {"Authorization": ("PVEAPIToken=engine@pve!aw=", Secret(CANARY))}},
    "a form's value": lambda: {"form": {"client_id": "k", "client_secret": Secret(CANARY)}},
    "one of a form's list": lambda: {"form": [("scope", ("devices", Secret(CANARY)))]},
}


def _went_whole(asked: Asked) -> bool:
    return CANARY in asked.headers.get("authorization", "") or CANARY.encode() in asked.body


def test_a_header_of_parts_and_a_form_s_secrets_reach_the_server_whole(serve, pki) -> None:
    server = serve()
    parts = ("PVEAPIToken=", "engine@pve!aw", "=", Secret(CANARY))
    form = {"client_id": "k", "client_secret": Secret(CANARY), "scope": ["devices:core", Secret(CANARY)], "n": 2}
    with _client(server, pki) as client:
        got = client.request("POST", "oauth/token", headers={"Authorization": parts, "X-Parts": ["a", "b"]}, form=form)
    assert got.status == 200
    [asked] = server.asked
    assert asked.headers["authorization"] == f"PVEAPIToken=engine@pve!aw={CANARY}" and asked.headers["x-parts"] == "ab"
    assert asked.body == f"client_id=k&client_secret={CANARY}&scope=devices%3Acore&scope={CANARY}&n=2".encode()
    assert asked.headers["content-length"] == str(len(asked.body))
    assert asked.headers["content-type"] == "application/x-www-form-urlencoded"
    # What was given keeps its Secrets: nothing of it was revealed in place.
    assert CANARY not in repr(form) and CANARY not in repr(parts)


@pytest.mark.parametrize("given", WITH_A_SECRET.values(), ids=WITH_A_SECRET.keys())
def test_a_secret_is_never_in_the_error_of_a_request_it_went_with(serve, pki, caplog, capsys, fast, given) -> None:
    caplog.set_level(logging.DEBUG)
    server = serve(drop)
    with _client(server, pki) as client, pytest.raises(Unknown) as unknown:
        client.request("POST", "pools", **given())
    assert unknown.value.__cause__ is None and unknown.value.__context__ is None
    assert not [text for text in _said(unknown.value, caplog, capsys) if CANARY in text]
    assert [_went_whole(one) for one in server.asked] == [True]


@pytest.mark.parametrize("line", ["HTTP/1.1 4x0 {}", "HTTP/{} 200 OK"], ids=["status no number", "version no HTTP's"])
@pytest.mark.parametrize("given", WITH_A_SECRET.values(), ids=WITH_A_SECRET.keys())
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_server_that_echoes_the_secret_in_its_status_line_has_none_of_it_said(
    serve, pki, caplog, capsys, fast, line, given, method
) -> None:
    """A server, or a proxy that inspects TLS and that the context trusts, puts what it was sent in a status line
    http.client cannot read: http.client's own error quotes that line, and the transport says what it means instead."""
    caplog.set_level(logging.DEBUG)

    def echo(handler, asked, nth):
        sent = asked.headers.get("authorization") or asked.body.decode()
        handler.wfile.write(line.format(sent).encode() + b"\r\n\r\n")
        handler.wfile.flush()
        handler.close_connection = True

    server = serve(echo)
    with _client(server, pki) as client, pytest.raises((Unreachable, Unknown)) as failed:
        client.request(method, "pools", **given())
    assert not [text for text in _said(failed.value, caplog, capsys) if CANARY in text]
    assert "it answered something that is no HTTP" in str(failed.value)
    assert server.asked and all(_went_whole(one) for one in server.asked)


def _holding_itself() -> list:
    """A list that holds itself, and then a Secret: each collection is walked once, so the walk ends and finds it."""
    held: list = []
    held.append(held)
    held.append(Secret(CANARY))
    return held


@pytest.mark.parametrize(
    "given",
    [
        {"query": {"t": Secret(CANARY)}},
        {"query": [("t", ["a", Secret(CANARY)])]},
        {"query": {"t": [["a", Secret(CANARY)]]}},
        {"query": {"t": {"k": Secret(CANARY)}}},
        {"query": [(Secret(CANARY), "v")]},
        {"query": {"t": _holding_itself()}},
        {"json": {"t": Secret(CANARY)}},
        {"form": {"t": {Secret(CANARY)}}},
        {"form": {"t": [[Secret(CANARY)]]}},
        {"form": {"t": {"k": Secret(CANARY)}}},
        {"form": {"t": ("a", {"k": Secret(CANARY)})}},
        {"form": [(Secret(CANARY), "v")]},
    ],
    ids=[
        "a query's value",
        "one of a query's list",
        "a list in a query's list",
        "a query's mapping",
        "a query's name",
        "a list that holds itself",
        "JSON",
        "a form's set",
        "a list in a form's list",
        "a form's mapping",
        "a mapping in a form's tuple",
        "a form's name",
    ],
)
def test_a_secret_never_goes_in_a_query_or_json_nor_in_a_form_but_as_a_value(serve, pki, given) -> None:
    server = serve()
    with _client(server, pki) as client, pytest.raises(TypeError) as refused:
        client.request("POST", "pools", **given)
    assert CANARY not in str(refused.value) and server.asked == []


def test_a_form_s_secret_that_cannot_be_encoded_is_not_made_and_not_said(serve, pki, caplog, capsys) -> None:
    caplog.set_level(logging.DEBUG)
    server = serve()
    with _client(server, pki) as client, pytest.raises(NotMade) as refused:
        client.request("POST", "pools", form={"t": Secret(f"{CANARY}\ud800")})
    assert refused.value.__cause__ is None and refused.value.__context__ is None
    assert not [text for text in _said(refused.value, caplog, capsys) if CANARY in text]
    assert server.asked == []


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "version\r\nX: y"), ("GET", "a path"), ("get", "version"), ("G\nET", "version")]
)
def test_a_path_or_a_method_http_client_would_refuse_is_not_made(serve, pki, method, path) -> None:
    server = serve()
    with _client(server, pki) as client, pytest.raises(NotMade):
        client.request(method, path)
    assert server.asked == []


# ---------------------------------------------------------------- the proxy chosen ----

URL = "https://pve.lan:8006/api2/json"
ADDRESS = "https://10.1.2.3:8006/api2/json"


@pytest.mark.parametrize(
    ("environ", "chosen"),
    [
        ({"HTTPS_PROXY": "http://p:3128"}, "http://p:3128"),
        ({"https_proxy": "http://p:3128"}, "http://p:3128"),
        ({"HTTPS_PROXY": "http://upper:1", "https_proxy": "http://lower:2"}, "http://lower:2"),
        ({"ALL_PROXY": "http://all:3128"}, "http://all:3128"),
        ({"all_proxy": "http://all:3128"}, "http://all:3128"),
        ({"ALL_PROXY": "http://all:1", "HTTPS_PROXY": "http://p:2"}, "http://p:2"),
        ({"HTTP_PROXY": "http://p:3128", "http_proxy": "http://p:3128"}, None),
        ({"HTTPS_PROXY": "p:3128"}, "http://p:3128"),
        ({"HTTPS_PROXY": "http://p:1", "https_proxy": ""}, None),
        ({"HTTPS_PROXY": "socks5://s:1080"}, "socks5://s:1080"),
        ({}, None),
    ],
)
def test_the_environment_s_proxy_is_its_https_entry_else_its_all_entry(environ, chosen) -> None:
    assert proxy.choose(proxy.ENVIRONMENT, environ, URL) == chosen


@pytest.mark.parametrize(
    ("no_proxy", "url", "direct"),
    [
        ({"NO_PROXY": "pve.lan"}, URL, True),
        ({"no_proxy": "pve.lan"}, URL, True),
        ({"NO_PROXY": ".lan"}, URL, True),
        ({"NO_PROXY": "lan"}, URL, True),
        ({"NO_PROXY": "ve.lan"}, URL, False),
        ({"NO_PROXY": "pve.lan:8006"}, URL, True),
        ({"NO_PROXY": " other.lan , pve.lan "}, URL, True),
        ({"NO_PROXY": "*"}, URL, True),
        ({"NO_PROXY": "other.lan"}, URL, False),
        ({"no_proxy": "other.lan", "NO_PROXY": "pve.lan"}, URL, False),
        ({"NO_PROXY": "10.0.0.0/8"}, ADDRESS, True),
        ({"NO_PROXY": "10.1.2.0/24"}, ADDRESS, True),
        ({"NO_PROXY": "10.1.2.3"}, ADDRESS, True),
        ({"NO_PROXY": "11.0.0.0/8"}, ADDRESS, False),
        ({"NO_PROXY": "10.1.2.3/33"}, ADDRESS, False),
        ({"NO_PROXY": "*"}, ADDRESS, True),
    ],
)
def test_what_no_proxy_names_goes_straight_to_the_server(no_proxy, url, direct) -> None:
    environ = {"HTTPS_PROXY": "http://p:3128", **no_proxy}
    assert proxy.choose(proxy.ENVIRONMENT, environ, url) == (None if direct else "http://p:3128")


def test_none_is_no_proxy_whatever_the_environment_says_and_a_url_is_that_proxy(system) -> None:
    system["proxies"] = {"https": "http://system:3128"}
    environ = {"HTTPS_PROXY": "http://p:3128", "ALL_PROXY": "http://all:3128"}
    assert proxy.choose(proxy.NONE, environ, URL) is None
    assert proxy.choose(proxy.NONE, {}, URL) is None
    assert proxy.choose("http://named:3128", environ, URL) == "http://named:3128"
    assert proxy.choose("http://named:3128", {"NO_PROXY": "*"}, URL) == "http://named:3128"


@pytest.mark.parametrize("setting", ["socks5://s:1080", "https://p:3128", "", "env", "http://"])
def test_a_setting_that_is_neither_word_nor_an_http_url_is_refused(setting) -> None:
    with pytest.raises(ValueError):
        proxy.choose(setting, {}, URL)


def test_choose_reads_only_the_environment_it_is_given(monkeypatch) -> None:
    for name in PROXY_VARIABLES:
        monkeypatch.setenv(name, "http://trap:1")
    monkeypatch.setenv("NO_PROXY", "pve.lan")
    assert proxy.choose(proxy.ENVIRONMENT, {}, URL) is None
    assert proxy.choose(proxy.ENVIRONMENT, {"HTTPS_PROXY": "http://given:3128"}, URL) == "http://given:3128"


def test_where_the_environment_names_no_proxy_the_system_s_is_taken(system) -> None:
    system["proxies"] = {"https": "http://system:3128", "http": "http://system:3128"}
    assert proxy.choose(proxy.ENVIRONMENT, {}, URL) == "http://system:3128"
    # The environment's, where it names any, as requests reads them: the system's are then not read.
    assert proxy.choose(proxy.ENVIRONMENT, {"HTTPS_PROXY": "http://env:1"}, URL) == "http://env:1"
    assert proxy.choose(proxy.ENVIRONMENT, {"NO_PROXY": "other.lan"}, URL) is None
    system["bypass"] = True
    assert proxy.choose(proxy.ENVIRONMENT, {}, URL) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's network settings")
def test_on_macos_the_system_s_proxy_is_the_one_the_standard_library_reads(monkeypatch) -> None:
    monkeypatch.setattr(proxy, "_system_proxies", SYSTEM_PROXIES)
    monkeypatch.setattr(proxy, "_system_bypass", SYSTEM_BYPASS)
    monkeypatch.setattr(urllib.request, "getproxies_macosx_sysconf", lambda: {"https": "http://mac:3128"})
    monkeypatch.setattr(urllib.request, "proxy_bypass_macosx_sysconf", lambda host: host == "pve.lan")
    assert proxy.choose(proxy.ENVIRONMENT, {}, "https://other.lan/") == "http://mac:3128"
    assert proxy.choose(proxy.ENVIRONMENT, {}, URL) is None


@pytest.mark.skipif(sys.platform == "darwin", reason="Linux has no proxy settings but the environment's")
def test_on_linux_there_is_no_system_proxy(monkeypatch) -> None:
    monkeypatch.setattr(proxy, "_system_proxies", SYSTEM_PROXIES)
    monkeypatch.setattr(proxy, "_system_bypass", SYSTEM_BYPASS)
    assert proxy.choose(proxy.ENVIRONMENT, {}, URL) is None


# ---------------------------------------------------------------- the rules ----


def _new_engine() -> list[Path]:
    """The new engine's Python: its subpackages of lib/claude_vm/, secret.py and tools/aw."""
    found = []
    for path in repo_files("lib/claude_vm", "tools/aw", untracked=True):
        rel = path.relative_to(REPO)
        if rel == Path("tools/aw") or rel == Path("lib/claude_vm/secret.py"):
            found.append(path)
        elif path.suffix == ".py" and len(rel.parts) > 3 and rel.parts[2] in NEW:
            found.append(path)
    return found


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def _imports(tree: ast.AST) -> set[str]:
    """Every module an import names, at the top or in a function: for from a import b, a and a.b both."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names |= {node.module, *(f"{node.module}.{alias.name}" for alias in node.names)}
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
            "import_module",
            "__import__",
        ):
            names |= {
                arg.value for arg in node.args[:1] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            }
    return names


def test_the_rules_read_the_new_engine() -> None:
    files = {_rel(path) for path in _new_engine()}
    assert {_rel(TRANSPORT), _rel(PROXY), "tools/aw", "lib/claude_vm/secret.py"} <= files, sorted(files)


def test_only_the_transport_speaks_http_client_or_wraps_a_socket() -> None:
    speaking, wrapping = [], []
    for path in _new_engine():
        tree = ast.parse(path.read_text())
        if any(name == "http.client" or name.startswith("http.client.") for name in _imports(tree)) or any(
            isinstance(node, ast.Attribute) and node.attr == "client" and getattr(node.value, "id", "") == "http"
            for node in ast.walk(tree)
        ):
            speaking.append(_rel(path))
        if any(isinstance(node, ast.Attribute) and node.attr in ("wrap_socket", "wrap_bio") for node in ast.walk(tree)):
            wrapping.append(_rel(path))
    assert speaking == [_rel(TRANSPORT)]
    assert set(wrapping) <= {_rel(TRANSPORT)}


def test_only_proxy_reads_urllib_request_and_only_its_proxy_readers() -> None:
    importing = [_rel(path) for path in _new_engine() if "urllib.request" in _imports(ast.parse(path.read_text()))]
    assert importing == [_rel(PROXY)]
    tree = ast.parse(PROXY.read_text())
    used, aliased, imported = set(), [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "request":
            if getattr(node.value.value, "id", "") == "urllib":
                used.add(node.attr)
        elif isinstance(node, ast.Import):
            aliased += [alias.name for alias in node.names if alias.name.startswith("urllib") and alias.asname]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("urllib"):
            imported |= {
                alias.name for alias in node.names if node.module == "urllib.request" or alias.name == "request"
            }
    assert aliased == [] and imported == set()
    assert used and used <= URLLIB_READERS, sorted(used - URLLIB_READERS)


def test_no_module_of_the_new_engine_names_a_certificate_variable() -> None:
    naming = [_rel(path) for path in _new_engine() if CERTIFICATE_VARIABLES.search(path.read_text())]
    assert naming == []


def test_the_transport_reveals_a_secret_in_one_place() -> None:
    assert TRANSPORT.read_text().count("reveal(") == 1


def _hands_a_check(tree: ast.AST) -> bool:
    """Whether tree calls Transport, by its name, a name it is imported as or an attribute, and hands it a check of the
    peer: peer_check by keyword, anything but None; a ** mapping or a * sequence, which may hold one; or more than
    seven arguments by position, the eighth being peer_check."""
    names = {"Transport"} | {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "Transport" and alias.asname
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if called not in names:
            continue
        if len(node.args) > 7 or any(isinstance(arg, ast.Starred) for arg in node.args):
            return True
        for keyword in node.keywords:
            given = keyword.value
            if keyword.arg is None or (
                keyword.arg == "peer_check" and not (isinstance(given, ast.Constant) and given.value is None)
            ):
                return True
    return False


@pytest.mark.parametrize(
    ("source", "hands"),
    [
        ("Transport(url, context=made, user_agent=AGENT, peer_check=held)", True),
        ("transport.Transport(url, made, AGENT, 5, 30, MOST, None, held)", True),
        ("from ..vendors.transport import Transport as Made\nMade(url, made, AGENT, peer_check=held)", True),
        ("Transport(url, made, AGENT, **given)", True),
        ("Transport(*given)", True),
        ("Transport(url, made, AGENT, peer_check=None)", False),
        ("Transport(url, made, AGENT, 5, 30, MOST, proxy)", False),
        ('"""This module hands the transport no peer_check."""', False),
        ("peer_check = held", False),
    ],
)
def test_a_check_handed_to_the_transport_is_found_by_the_call_that_hands_it(source, hands) -> None:
    assert _hands_a_check(ast.parse(source)) is hands


def test_only_the_modules_peer_checks_names_hand_the_transport_a_check() -> None:
    handing = {
        _rel(path) for path in _new_engine() if path != TRANSPORT and _hands_a_check(ast.parse(path.read_text()))
    }
    assert handing == set(PEER_CHECKS), "a module that hands the transport a check of the peer is in PEER_CHECKS"
    assert all(reason.strip() for reason in PEER_CHECKS.values())
