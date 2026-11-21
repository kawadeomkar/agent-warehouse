"""The vendors' trust: one certificate file, certifi's by its path unless the setting tls_ca_file names another, and a
client context that verifies against that file alone (lib/claude_vm/vendors/trust.py; docs/architecture/engine.md
§16.7).

Each server is a TLS server this test starts in its own process, on loopback, with a CA and the node's certificate
tests/unit/pki.py makes, and it is reached through the one transport, as the vendors' calls are. The interpreter's own
store is the one the variables below change, a file (SSL_CERT_FILE) and a folder read by subject hash (SSL_CERT_DIR),
which a context made here never reads: a root put in either and not in the file is refused, and the refusal names
tls_ca_file. The rules at the end hold vendors/ to that, certifi to one importer, and the new engine to the makers of a
TLS context CONTEXTS names, by what a module writes and by which of today's modules that make one it imports.
"""

from __future__ import annotations

import ast
import datetime
import http.server
import re
import shutil
import ssl
import subprocess
import sys
import threading
from pathlib import Path

import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from harness import REPO, repo_files
from pki import proxmox_pki
from test_aw_layout import _imported, _module_files, _package, _reached, _reused_modules, _within
from test_aw_transport import _imports, _new_engine, _rel

from claude_vm.vendors import trust
from claude_vm.vendors.transport import Transport, Unreachable, Untrusted

# The module's two certificate authorities: the server's, and one that signed nothing the server shows. Cheap enough
# to make again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "other_pki"}
AGENT = "agent-warehouse-test"
TRUST = REPO / "lib" / "claude_vm" / "vendors" / "trust.py"
# What would give a context the interpreter's own store, or its paths: never called, nor named, in vendors/.
DEFAULT_STORE = ("create_default_context", "load_default_certs", "set_default_verify_paths", "get_default_verify_paths")
# Every variable a client library or OpenSSL reads for a file of certificates: traps here, with SSL_CERT_DIR naming
# their folder and SSLKEYLOGFILE a log of the session's keys.
VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
# The modules of the new engine that make a TLS context, give one roots or set its checks, or import one of today's
# modules that does, each with why: the vendors' context, made in one place; and the Proxmox client's pin.
CONTEXTS: dict[str, str] = {
    "lib/claude_vm/vendors/trust.py": "the vendors' one context, made by hand from one certificate file, the server's"
    " name checked and a certificate required",
    "lib/claude_vm/pve/pin.py": "the Proxmox client's context: the host's anchor alone, made by today's trust.py"
    " (anchored_context), the one way a context is made from a certificate the host gave; else the vendors' one",
}
# What makes a TLS context or gives one roots, by any spelling but as a type; and the checks a context is loosened by.
MAKERS = frozenset(
    {
        "SSLContext",
        "create_default_context",
        "_create_unverified_context",
        "_create_default_https_context",
        "load_verify_locations",
    }
)
# Today's modules of lib/claude_vm/ that the new engine may import (test_aw_layout.py's REUSED) and that make a TLS
# context, give one roots or set its checks, or import one that does, each with why: a module of the new engine that
# imports one can have a context made where no name above is written, so it is one CONTEXTS names.
TODAYS_CONTEXTS = {
    "trust": "the certificate init keeps for the Proxmox API, tried in contexts made as the run's clients make theirs",
    "status": "just status's engine, whose reads of the Proxmox API verify against the certificate kept, or not",
}
CHECKS = frozenset({"check_hostname", "verify_mode"})


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(scope="module")
def other_pki(tmp_path_factory):
    """A CA of another subject than the server's: OpenSSL finds no issuer of the server's name in a file of it, and so
    asks every other source a context has."""
    return proxmox_pki(tmp_path_factory.mktemp("other-pki"), key_usage=True, name="Another Test CA")


class _Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        pass  # a client that refused the certificate went first: nothing to print


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def serve():
    """start(pki) starts a TLS server on loopback with that pki's node certificate, and gives its address."""
    servers = []

    def start(pki: dict) -> str:
        server = _Server(("127.0.0.1", 0), _Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(pki["cert"], pki["key"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return f"https://127.0.0.1:{server.server_address[1]}/api"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _get(url: str, context: ssl.SSLContext) -> int:
    """A GET through the one transport with context: the status the server answered."""
    with Transport(url, context=context, user_agent=AGENT) as client:
        return client.request("GET", "version").status


def _file(directory: Path, name: str, *pems: str) -> Path:
    path = directory / name
    path.write_text("".join(pems))
    return path


def _der(pem: str) -> bytes:
    return ssl.PEM_cert_to_DER_cert(pem)


def _trap(monkeypatch, directory: Path, pem: str) -> Path:
    """Every variable a library reads for its certificates names a file of pem, SSL_CERT_DIR their folder, and
    SSLKEYLOGFILE the log of keys this gives. OpenSSL reads a folder by subject hash alone, so that folder holds nothing
    it reads: _hashed makes one that does."""
    for name in VARIABLES:
        monkeypatch.setenv(name, str(_file(directory, f"trap-{name}.pem", pem)))
    monkeypatch.setenv("SSL_CERT_DIR", str(directory))
    monkeypatch.setenv("SSLKEYLOGFILE", str(directory / "keys.log"))
    return directory / "keys.log"


def _hashed(directory: Path, pem: str) -> Path:
    """A folder that holds pem as OpenSSL reads a certificate folder: named <subject hash>.0, the hash as openssl x509
    -subject_hash prints it. The test is skipped where there is no openssl."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("no openssl to name a certificate by its subject's hash")
    folder = directory / "hashed"
    folder.mkdir()
    ca = _file(directory, "hashed-ca.pem", pem)
    said = subprocess.run(
        [openssl, "x509", "-subject_hash", "-noout", "-in", str(ca)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{8}", said), said
    (folder / f"{said}.0").write_text(pem)
    return folder


# ---------------------------------------------------------------- the context ----


def test_a_server_whose_root_is_in_the_file_verifies(serve, pki, tmp_path) -> None:
    url = serve(pki)
    assert _get(url, trust.context(_file(tmp_path, "ca.pem", pki["ca"]))) == 200


def test_whatever_the_variables_name_a_server_whose_root_is_in_the_file_verifies_and_no_key_is_logged(
    serve, pki, other_pki, tmp_path, monkeypatch
) -> None:
    url = serve(pki)
    keys = _trap(monkeypatch, tmp_path, other_pki["ca"])
    made = trust.context(_file(tmp_path, "ca.pem", pki["ca"]))
    assert _get(url, made) == 200
    assert made.keylog_filename is None and not keys.exists()


def test_with_the_interpreter_s_store_variable_naming_an_empty_file_the_server_still_verifies(
    serve, pki, tmp_path, monkeypatch
) -> None:
    url = serve(pki)
    empty = _file(tmp_path, "empty.pem")
    (tmp_path / "no-certificates").mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(empty))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "no-certificates"))
    assert _get(url, trust.context(_file(tmp_path, "ca.pem", pki["ca"]))) == 200


@pytest.mark.parametrize("half", ["file", "folder"])
@pytest.mark.parametrize("given", ["certifi", "other"])
def test_a_server_whose_root_is_in_the_interpreter_s_store_and_not_in_the_file_is_refused(
    serve, pki, other_pki, tmp_path, monkeypatch, given, half
) -> None:
    """The server's root is put in one half of the interpreter's own store, the file SSL_CERT_FILE names or the folder
    SSL_CERT_DIR names (by its subject hash, as OpenSSL reads one), which a default context then trusts; and
    REQUESTS_CA_BUNDLE and CURL_CA_BUNDLE name it too, as traps. A context made from certifi's file, or from a file of
    a root of another subject, which OpenSSL would look past to any other source, still refuses the server: none of
    them is read. The refusal is Untrusted, and says what to set tls_ca_file to."""
    url = serve(pki)
    _trap(monkeypatch, tmp_path, pki["ca"])
    if half == "folder":
        monkeypatch.setenv("SSL_CERT_FILE", str(_file(tmp_path, "empty.pem")))
        monkeypatch.setenv("SSL_CERT_DIR", str(_hashed(tmp_path, pki["ca"])))
    assert _get(url, ssl.create_default_context()) == 200  # the root is in that half of the interpreter's store
    path = trust.ca_file(None) if given == "certifi" else _file(tmp_path, "other.pem", other_pki["ca"])
    with pytest.raises(Untrusted) as refused:
        _get(url, trust.context(path))
    said = str(refused.value)
    assert "certificate does not verify" in said and "set tls_ca_file to a file of PEM certificates" in said


def test_a_server_whose_certificate_names_another_host_is_refused(serve, tmp_path) -> None:
    misnamed = proxmox_pki(tmp_path / "pki", key_usage=True, address="127.0.0.2")
    url = serve(misnamed)
    with pytest.raises(Unreachable) as refused:
        _get(url, trust.context(_file(tmp_path, "ca.pem", misnamed["ca"])))
    assert "certificate does not verify" in str(refused.value)


def test_the_context_checks_the_name_and_requires_a_certificate_strictly_on_every_python(pki, tmp_path) -> None:
    made = trust.context(_file(tmp_path, "ca.pem", pki["ca"]))
    assert made.check_hostname is True and made.verify_mode == ssl.CERT_REQUIRED
    assert made.verify_flags & ssl.VERIFY_X509_STRICT and made.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert made.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_a_proxmox_ca_with_no_key_usage_is_refused_as_python_3_13_refuses_it(serve, tmp_path) -> None:
    """The strict flags hold on every Python: a CA as Proxmox VE 8 makes one, with no keyUsage, does not verify, so an
    interpreter before 3.13 is no looser than today's requests on 3.13."""
    old = proxmox_pki(tmp_path / "pki", key_usage=False)
    url = serve(old)
    with pytest.raises(Unreachable):
        _get(url, trust.context(_file(tmp_path, "ca.pem", old["ca"])))


# ---------------------------------------------------------------- the file ----


def test_with_no_setting_the_file_is_certifi_s_by_its_path_and_it_loads() -> None:
    assert trust.ca_file(None) == trust.ca_file("") == certifi.where()
    assert trust.context(trust.ca_file(None)).cert_store_stats()["x509_ca"] > 0


def test_a_setting_names_the_file_and_that_file_alone_is_loaded(pki, other_pki, tmp_path, monkeypatch) -> None:
    own = _file(tmp_path, "own.pem", pki["ca"])
    _trap(monkeypatch, tmp_path, other_pki["ca"])
    assert trust.ca_file(own) == trust.ca_file(str(own)) == str(own)
    made = trust.context(trust.ca_file(own))
    assert made.get_ca_certs(binary_form=True) == [_der(pki["ca"])]
    assert made.cert_store_stats() == {"x509": 1, "crl": 0, "x509_ca": 1}


def test_the_variables_change_neither_the_file_nor_what_it_loads(pki, other_pki, tmp_path, monkeypatch) -> None:
    own = _file(tmp_path, "own.pem", pki["ca"])
    before = [trust.ca_file(None), trust.ca_file(own)]
    loaded = [trust.context(path).get_ca_certs(binary_form=True) for path in before]
    _trap(monkeypatch, tmp_path, other_pki["ca"])
    assert [trust.ca_file(None), trust.ca_file(own)] == before
    assert [trust.context(path).get_ca_certs(binary_form=True) for path in before] == loaded


def _crl() -> str:
    """A certificate revocation list alone: a file OpenSSL loads, which holds no certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent-warehouse test")])
    now = datetime.datetime.now(datetime.UTC)
    builder = x509.CertificateRevocationListBuilder().issuer_name(name).last_update(now)
    crl = builder.next_update(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256())
    return crl.public_bytes(serialization.Encoding.PEM).decode()


@pytest.mark.parametrize("held", ["nothing", "text", "a revocation list", "a folder"])
def test_a_file_that_holds_no_certificate_is_unusable_and_names_the_setting(tmp_path, held) -> None:
    if held == "a folder":
        path = tmp_path / "certificates"
        path.mkdir()
    else:
        text = _crl() if held == "a revocation list" else {"nothing": "", "text": "not a certificate\n"}[held]
        path = _file(tmp_path, "ca.pem", text)
    with pytest.raises(trust.Unusable) as unusable:
        trust.context(trust.ca_file(path))
    assert "holds no certificate" in str(unusable.value) and "tls_ca_file" in str(unusable.value)


def test_a_setting_that_names_nothing_is_unusable_and_names_the_setting(tmp_path) -> None:
    missing = tmp_path / "nowhere" / "ca.pem"
    with pytest.raises(trust.Unusable) as unusable:
        trust.context(trust.ca_file(missing))
    assert "is not there" in str(unusable.value) and "tls_ca_file" in str(unusable.value)
    assert repr(str(missing)) in str(unusable.value)


def test_without_certifi_and_no_setting_the_file_is_unusable_and_names_the_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "certifi", None)
    with pytest.raises(trust.Unusable) as unusable:
        trust.ca_file(None)
    assert "tls_ca_file" in str(unusable.value)
    own = _file(tmp_path, "own.pem", "")
    assert trust.ca_file(own) == str(own)


# ---------------------------------------------------------------- the rules ----


def _vendors() -> list[Path]:
    return [path for path in repo_files("lib/claude_vm/vendors", untracked=True) if path.suffix == ".py"]


def test_the_rules_read_vendors_and_the_new_engine() -> None:
    assert TRUST in _vendors() and TRUST in _new_engine()


def test_vendors_never_asks_for_the_interpreter_s_own_store() -> None:
    naming = {_rel(path): name for path in _vendors() for name in DEFAULT_STORE if name in path.read_text()}
    assert naming == {}


def _as_a_type(tree: ast.AST) -> set[int]:
    """The nodes of tree that name a type and make nothing: an annotation, and what isinstance or issubclass test."""
    typed: set[int] = set()
    for node in ast.walk(tree):
        shown = []
        if isinstance(node, ast.arg | ast.AnnAssign) and node.annotation is not None:
            shown.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            shown.append(node.returns)
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("isinstance", "issubclass"):
            shown += node.args[1:2]
        typed |= {id(one) for part in shown for one in ast.walk(part)}
    return typed


def _makes_a_context(tree: ast.AST) -> list[str]:
    """What of tree makes a TLS context or gives one roots, a name of MAKERS by any spelling (a call, an attribute, an
    alias, an import, a string) but as a type; and what sets a context's checks, CHECKS assigned or named as a
    string."""
    typed = _as_a_type(tree)
    found = []
    for node in ast.walk(tree):
        if id(node) in typed:
            continue
        if isinstance(node, ast.Name):
            named = node.id
        elif isinstance(node, ast.Attribute):
            named = node.attr
            if named in CHECKS and not isinstance(node.ctx, ast.Load):
                found.append(named)
        elif isinstance(node, ast.alias):
            named = node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            named = node.value
            if named in CHECKS:
                found.append(named)
        else:
            continue
        if named in MAKERS:
            found.append(named)
    return found


@pytest.mark.parametrize(
    ("source", "makes"),
    [
        ("ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)", True),
        ("make = ssl.SSLContext\nmake(2)", True),
        ("from ssl import SSLContext as Made", True),
        ("getattr(ssl, 'SSLContext')(2)", True),
        ("ssl.create_default_context()", True),
        ("ssl._create_unverified_context()", True),
        ("context.load_verify_locations(cafile=path)", True),
        ("context.check_hostname = False", True),
        ("context.verify_mode = ssl.CERT_NONE", True),
        ("setattr(context, 'verify_mode', 0)", True),
        ("isinstance(context, ssl.SSLContext)", False),
        ("def f(context: ssl.SSLContext) -> ssl.SSLContext:\n    return context", False),
        ("made: 'ssl.SSLContext' = given", False),
        ("strict = context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED", False),
        ("tls = context.wrap_socket(sock, server_hostname=host)", False),
    ],
)
def test_what_makes_a_tls_context_is_found_by_any_spelling(source, makes) -> None:
    assert bool(_makes_a_context(ast.parse(source))) is makes


def _todays_making() -> set[str]:
    """Each module of today's that the new engine reaches through REUSED and that names a maker or a check, or imports
    one that does, all the way up."""
    reached = sorted(_reached(_reused_modules()))
    trees = {
        module: (ast.parse(_module_files(module)[0].read_text()), _package(_module_files(module)[0]))
        for module in reached
    }
    making = {module for module, (tree, _) in trees.items() if _makes_a_context(tree)}
    grown = True
    while grown:
        grown = False
        for module, (tree, package) in trees.items():
            if module not in making and making & set(_imported(tree, package)):
                making.add(module)
                grown = True
    return making


def test_todays_contexts_names_every_module_of_today_that_makes_one() -> None:
    making = _todays_making()
    assert {"claude_vm.trust", "claude_vm.status.engine"} <= making, sorted(making)
    assert sorted(module for module in making if not _within(module, TODAYS_CONTEXTS)) == []
    assert [name for name in TODAYS_CONTEXTS if not any(_within(module, (name,)) for module in making)] == []
    assert all(reason.strip() for reason in TODAYS_CONTEXTS.values())


def _imports_todays_context(path: Path) -> list[str]:
    """The modules of TODAYS_CONTEXTS a file of the new engine imports, relative imports resolved."""
    tree = ast.parse(path.read_text())
    return sorted(
        {module for module in _imported(tree, _package(path), every=True) if _within(module, TODAYS_CONTEXTS)}
    )


@pytest.mark.parametrize(
    ("source", "imports"),
    [
        ("from .. import trust", True),
        ("from ..trust import _context as made", True),
        ("import claude_vm.status.engine", True),
        ("from ..status import engine", True),
        ("importlib.import_module('claude_vm.trust')", True),
        ("from ..vendors import trust", False),
        ("from .. import proxmox", False),
    ],
)
def test_an_import_of_todays_contexts_is_found_relative_or_whole(source, imports) -> None:
    path = REPO / "lib" / "claude_vm" / "pve" / "probe.py"
    assert _package(path) == "claude_vm.pve"
    tree = ast.parse(source)
    found = {module for module in _imported(tree, _package(path), every=True) if _within(module, TODAYS_CONTEXTS)}
    assert bool(found) is imports


def test_only_the_modules_contexts_names_make_a_tls_context() -> None:
    """The vendors' context is trust.py's alone, and the Proxmox client's pin.py's: no other module of the new engine
    makes one, gives one roots or loosens one's checks, or imports one of today's that does, and hands it to the
    transport, which takes any context."""
    making = {
        _rel(path): _makes_a_context(ast.parse(path.read_text())) + _imports_todays_context(path)
        for path in _new_engine()
    }
    assert {rel for rel, found in making.items() if found} == set(CONTEXTS), (
        "a module that makes a TLS context is named in CONTEXTS, with why: "
        f"{ {rel: found for rel, found in making.items() if found and rel not in CONTEXTS} }"
    )
    assert all(reason.strip() for reason in CONTEXTS.values())


def test_certifi_is_imported_by_vendors_trust_alone() -> None:
    importing = [
        _rel(path)
        for path in _new_engine()
        if any(name == "certifi" or name.startswith("certifi.") for name in _imports(ast.parse(path.read_text())))
    ]
    assert importing == [_rel(TRUST)]
