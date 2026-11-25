"""The computer's certificates: certifi's list by its path, with the file the setting tls_ca_file names added to it,
and a client context that verifies against those files alone (lib/claude_vm/vendors/trust.py;
docs/architecture/engine.md §16.7).

Each server is a TLS server this test starts in its own process, on loopback, with a CA and the node's certificate
tests/unit/pki.py makes, and it is reached through the one transport, as the vendors' calls are. A root in the setting's
file alone verifies, and so does one of certifi's with the setting set. The interpreter's own store is the one the
variables below change, a file (SSL_CERT_FILE) and a folder read by subject hash (SSL_CERT_DIR), which a context made
here never reads: a root put in either and in neither file is refused, and the refusal, for an issuer OpenSSL does not
know and for nothing else, says to add it with tls_ca_file. The rules at the end hold the new engine to never naming
that store, certifi to one importer, and the new engine to the makers of a TLS context CONTEXTS names, by what a module
writes, by which of today's modules that make one it imports, and by what it takes of today's trust or status from a
module CONTEXTS names; and a module CONTEXTS names to taking names of today's modules, never a module whole, so that
none of today's is reached through its namespace.
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
from test_aw_layout import _exists, _imported, _module_files, _package, _reached, _reused_modules, _within
from test_aw_transport import _imports, _new_engine, _rel

from claude_vm.vendors import trust
from claude_vm.vendors.transport import UNKNOWN_ISSUER, Transport, Unreachable, Untrusted

# The module's two certificate authorities: the server's, and one that signed nothing the server shows. Cheap enough
# to make again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "other_pki"}
AGENT = "agent-warehouse-test"
TRUST = REPO / "lib" / "claude_vm" / "vendors" / "trust.py"
# What would give a context the interpreter's own store, or its paths: never called, nor named, in the new engine,
# CONTEXTS' modules included.
DEFAULT_STORE = ("create_default_context", "load_default_certs", "set_default_verify_paths", "get_default_verify_paths")
# Every variable a client library or OpenSSL reads for a file of certificates: traps here, with SSL_CERT_DIR naming
# their folder and SSLKEYLOGFILE a log of the session's keys.
VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
# The modules of the new engine that make a TLS context, give one roots or set its checks, or import one of today's
# modules that does, each with why: the vendors' context, made in one place; and the Proxmox client's pin.
CONTEXTS: dict[str, str] = {
    "lib/claude_vm/vendors/trust.py": "the vendors' one context, made by hand from the computer's certificates"
    " (certifi's list, with tls_ca_file's file added), the server's name checked and a certificate required",
    "lib/claude_vm/pve/pin.py": "the Proxmox client's context: the host's anchor alone, made by today's trust.py"
    " (anchored_context), the one way a context is made from a certificate the host gave; else the vendors' one;"
    " its name check off only where a recorded fingerprint names the one certificate, which the peer check holds:"
    " so a Pin's context is read by pin.py alone (test_a_pin_s_context_is_read_by_pin_py_alone)",
    "lib/claude_vm/engine/certificate.py": "init's certificate step: today's trust.py's probes, which carry no"
    " credential, by name (pick, presented, shown and its probe of a PEM text, never its store); it makes no context"
    " itself, and holds what it keeps to the client's through pin.kept"
    " (tests/unit/test_aw_engine_certificate.py)",
    "lib/claude_vm/guest/states.py": "a machine's states as status says them: one name of today's status, found.py's"
    " ssh_found, which makes no context and imports none of status that does (its engine reads the Proxmox API); it"
    " makes no context itself",
    "lib/claude_vm/cli/status.py": "aw status's entry: one name of today's status, its engine's run, whose reads of the"
    " Proxmox API verify against the host's anchor kept in the fleet's folder, or the computer's store; it makes no"
    " context itself (G16 moves the reads to the Proxmox client's)",
}
# What makes a TLS context or gives one roots, by any written spelling but as a type: the class, the standard library's
# ready-made contexts and its unverified ones (_create_stdlib_context is another name of one), get_server_certificate,
# which makes one of those to connect with, and the three ways to give one roots. A call's ssl=True, asyncio's way to a
# ready-made context, is one too (_makes_a_context).
MAKERS = frozenset(
    {
        "SSLContext",
        "create_default_context",
        "_create_unverified_context",
        "_create_default_https_context",
        "_create_stdlib_context",
        "get_server_certificate",
        "load_verify_locations",
        "load_default_certs",
        "set_default_verify_paths",
    }
)
# Today's modules of lib/claude_vm/ that the new engine may import (test_aw_layout.py's REUSED) and that make a TLS
# context, give one roots or set its checks, or import one that does, each with why: a module of the new engine that
# imports one can have a context made where no name above is written, so it is one CONTEXTS names.
TODAYS_CONTEXTS = {
    "trust": "the certificate init keeps for the Proxmox API, tried in contexts made as the run's clients make theirs",
    "status": "just status's engine, whose reads of the Proxmox API verify against the certificate kept, or not",
}
# What loosens a context's checks once it is made: its name check, whether a certificate is required, its flags
# (VERIFY_X509_STRICT among them), and a name taken from the subject's common name.
CHECKS = frozenset({"check_hostname", "verify_mode", "verify_flags", "hostname_checks_common_name"})


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


def test_a_server_signed_by_a_root_in_tls_ca_file_alone_verifies(serve, pki, tmp_path) -> None:
    url = serve(pki)
    assert _get(url, trust.context(_file(tmp_path, "ca.pem", pki["ca"]))) == 200


def test_with_tls_ca_file_set_a_server_signed_by_a_root_of_certifi_s_still_verifies(
    serve, pki, other_pki, tmp_path, monkeypatch
) -> None:
    """The setting adds to certifi's list, never takes its place: certifi's file is stood in for by one that holds the
    server's root, as a public root would be there, and the setting names a company's root of another subject."""
    url = serve(pki)
    stood_in = _file(tmp_path, "certifi.pem", pki["ca"])
    monkeypatch.setattr(certifi, "where", lambda: str(stood_in))
    company = _file(tmp_path, "company.pem", other_pki["ca"])
    assert _get(url, trust.context(company)) == 200
    assert _get(url, trust.context(None)) == 200


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
def test_a_server_whose_root_is_in_the_interpreter_s_store_and_not_in_the_files_is_refused(
    serve, pki, other_pki, tmp_path, monkeypatch, given, half
) -> None:
    """The server's root is put in one half of the interpreter's own store, the file SSL_CERT_FILE names or the folder
    SSL_CERT_DIR names (by its subject hash, as OpenSSL reads one), which a default context then trusts; and
    REQUESTS_CA_BUNDLE and CURL_CA_BUNDLE name it too, as traps. A context made from certifi's file, alone or with a
    file of a root of another subject, which OpenSSL would look past to any other source, still refuses the server: none
    of them is read. The refusal is Untrusted, for an issuer OpenSSL does not know, and says to add the root with
    tls_ca_file."""
    url = serve(pki)
    _trap(monkeypatch, tmp_path, pki["ca"])
    if half == "folder":
        monkeypatch.setenv("SSL_CERT_FILE", str(_file(tmp_path, "empty.pem")))
        monkeypatch.setenv("SSL_CERT_DIR", str(_hashed(tmp_path, pki["ca"])))
    assert _get(url, ssl.create_default_context()) == 200  # the root is in that half of the interpreter's store
    setting = None if given == "certifi" else _file(tmp_path, "other.pem", other_pki["ca"])
    with pytest.raises(Untrusted) as refused:
        _get(url, trust.context(setting))
    said = str(refused.value)
    assert refused.value.code in UNKNOWN_ISSUER
    assert said.startswith(f"its certificate does not verify ({refused.value.reason}): ")
    assert "add the certificate that issued it - that root, or the intermediate the server does not send" in said
    assert "file of PEM certificates the setting tls_ca_file names" in said
    assert "which is added to certifi's list" in said


@pytest.mark.parametrize("wrong", ["names another host", "has expired"])
def test_a_certificate_that_fails_for_another_reason_is_refused_with_its_reason_alone(serve, tmp_path, wrong) -> None:
    """A root of the person's own mends an issuer OpenSSL does not know, and nothing else: a certificate of another
    name, or one that has expired, signed by a root the file holds, is Untrusted with OpenSSL's reason and no word of
    tls_ca_file."""
    made = {"names another host": {"address": "127.0.0.2"}, "has expired": {"expired": True}}[wrong]
    other = proxmox_pki(tmp_path / "pki", key_usage=True, **made)
    url = serve(other)
    with pytest.raises(Untrusted) as refused:
        _get(url, trust.context(_file(tmp_path, "ca.pem", other["ca"])))
    said = str(refused.value)
    assert refused.value.code and refused.value.code not in UNKNOWN_ISSUER
    assert said == f"its certificate does not verify ({refused.value.reason})"
    assert {"names another host": "mismatch", "has expired": "expired"}[wrong] in refused.value.reason
    assert "tls_ca_file" not in said and "PEM" not in said


def test_the_context_checks_the_name_and_requires_a_certificate_strictly_on_every_python(pki, tmp_path) -> None:
    for made in (trust.context(None), trust.context(_file(tmp_path, "ca.pem", pki["ca"]))):
        assert made.check_hostname is True and made.verify_mode == ssl.CERT_REQUIRED
        assert made.verify_flags & ssl.VERIFY_X509_STRICT and made.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
        assert made.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_a_proxmox_ca_with_no_key_usage_is_refused_as_python_3_13_refuses_it(serve, tmp_path) -> None:
    """The strict flags hold on a context made by hand: a CA as Proxmox VE 8 makes one, with no keyUsage, does not
    verify, as it does not for today's requests."""
    old = proxmox_pki(tmp_path / "pki", key_usage=False)
    url = serve(old)
    with pytest.raises(Unreachable):
        _get(url, trust.context(_file(tmp_path, "ca.pem", old["ca"])))


# ---------------------------------------------------------------- the files ----


def _certificates(made: ssl.SSLContext) -> set[bytes]:
    """The CA certificates a context holds, as DER."""
    return set(made.get_ca_certs(binary_form=True))


def test_with_no_setting_the_file_is_certifi_s_by_its_path_and_it_loads() -> None:
    assert trust.files(None) == trust.files("") == (certifi.where(),)
    assert trust.context(None).cert_store_stats()["x509_ca"] > 0
    assert _certificates(trust.context("")) == _certificates(trust.context(None))


def test_a_setting_s_file_is_added_to_certifi_s_and_nothing_else_is_loaded(
    pki, other_pki, tmp_path, monkeypatch
) -> None:
    own = _file(tmp_path, "own.pem", pki["ca"])
    _trap(monkeypatch, tmp_path, other_pki["ca"])
    assert trust.files(own) == trust.files(str(own)) == (certifi.where(), str(own))
    alone, made = trust.context(None), trust.context(own)
    assert _certificates(made) == _certificates(alone) | {_der(pki["ca"])}
    assert _der(other_pki["ca"]) not in _certificates(made)
    assert made.cert_store_stats()["x509"] == alone.cert_store_stats()["x509"] + 1


def test_a_file_of_certificates_certifi_s_list_already_holds_is_no_fault(tmp_path) -> None:
    copy = _file(tmp_path, "copy.pem", Path(certifi.where()).read_text())
    assert trust.context(copy).cert_store_stats() == trust.context(None).cert_store_stats()


def test_the_variables_change_neither_the_files_nor_what_they_load(pki, other_pki, tmp_path, monkeypatch) -> None:
    own = _file(tmp_path, "own.pem", pki["ca"])
    before = [trust.files(None), trust.files(own)]
    loaded = [_certificates(trust.context(setting)) for setting in (None, own)]
    _trap(monkeypatch, tmp_path, other_pki["ca"])
    assert [trust.files(None), trust.files(own)] == before
    assert [_certificates(trust.context(setting)) for setting in (None, own)] == loaded


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
        trust.context(path)
    said = str(unusable.value)
    assert "holds no certificate" in said and "tls_ca_file" in said and repr(str(path)) in said


def test_a_setting_that_names_nothing_is_unusable_and_names_the_setting(tmp_path) -> None:
    missing = tmp_path / "nowhere" / "ca.pem"
    with pytest.raises(trust.Unusable) as unusable:
        trust.context(missing)
    said = str(unusable.value)
    assert "is not there" in said and "tls_ca_file" in said and repr(str(missing)) in said


def test_without_certifi_every_context_is_unusable_and_names_aw_install(monkeypatch, tmp_path) -> None:
    """certifi's list is always loaded, a setting or none: without certifi no context is made, and the remedy is the
    one the command line gives for any package its environment lacks."""
    own = _file(tmp_path, "own.pem", "")
    monkeypatch.setitem(sys.modules, "certifi", None)
    for made in (lambda: trust.files(None), lambda: trust.files(own), lambda: trust.context(own)):
        with pytest.raises(trust.Unusable) as unusable:
            made()
        said = str(unusable.value)
        assert "certifi" in said and said.endswith("run aw install") and "tls_ca_file" not in said


@pytest.mark.parametrize("held", ["nothing there", "no certificate"])
def test_a_certifi_whose_file_cannot_be_used_is_unusable_and_names_aw_install(monkeypatch, tmp_path, held) -> None:
    path = tmp_path / "nowhere" / "cacert.pem" if held == "nothing there" else _file(tmp_path, "cacert.pem", "x\n")
    monkeypatch.setattr(certifi, "where", lambda: str(path))
    with pytest.raises(trust.Unusable) as unusable:
        trust.context(None)
    said = str(unusable.value)
    assert said.startswith(f"certifi's certificate file {str(path)!r} ") and said.endswith("run aw install")


# ---------------------------------------------------------------- the rules ----


def _vendors() -> list[Path]:
    return [path for path in repo_files("lib/claude_vm/vendors", untracked=True) if path.suffix == ".py"]


def test_the_rules_read_vendors_and_the_new_engine() -> None:
    assert TRUST in _vendors() and TRUST in _new_engine()
    assert set(_vendors()) <= set(_new_engine())


def test_the_new_engine_never_asks_for_the_interpreter_s_own_store() -> None:
    """Not even a module CONTEXTS names: the computer's certificates are certifi's list and tls_ca_file's, and no
    context is ever given the interpreter's store, whose variables and folders the environment changes."""
    naming = {_rel(path): name for path in _new_engine() for name in DEFAULT_STORE if name in path.read_text()}
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
    alias, an import, a string) but as a type, or a call's ssl=True; and what sets a context's checks, CHECKS assigned
    (augmented too) or deleted, or named as a string."""
    typed = _as_a_type(tree)
    found = []
    for node in ast.walk(tree):
        if id(node) in typed:
            continue
        if isinstance(node, ast.keyword) and node.arg == "ssl":
            if isinstance(node.value, ast.Constant) and node.value.value is True:
                found.append("ssl=True")
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
        ("ssl._create_stdlib_context()", True),
        ("ssl.get_server_certificate((host, 443))", True),
        ("context.load_verify_locations(cafile=path)", True),
        ("context.load_default_certs()", True),
        ("context.set_default_verify_paths()", True),
        ("context.check_hostname = False", True),
        ("context.verify_mode = ssl.CERT_NONE", True),
        ("setattr(context, 'verify_mode', 0)", True),
        ("context.verify_flags = 0", True),
        ("context.verify_flags &= ~ssl.VERIFY_X509_STRICT", True),
        ("context.hostname_checks_common_name = True", True),
        ("del context.verify_flags", True),
        ("asyncio.open_connection(host, 443, ssl=True)", True),
        ("isinstance(context, ssl.SSLContext)", False),
        ("def f(context: ssl.SSLContext) -> ssl.SSLContext:\n    return context", False),
        ("made: 'ssl.SSLContext' = given", False),
        ("strict = context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED", False),
        ("strict = bool(context.verify_flags & ssl.VERIFY_X509_STRICT)", False),
        ("tls = context.wrap_socket(sock, server_hostname=host)", False),
        ("asyncio.open_connection(host, 443, ssl=made)", False),
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


def _contexts_modules() -> set[str]:
    """The modules CONTEXTS names, by their names."""
    return {rel.removeprefix("lib/").removesuffix(".py").replace("/", ".") for rel in CONTEXTS}


def _base(node: ast.ImportFrom, package: str | None) -> str:
    """The module an ImportFrom takes its names from, by its absolute name."""
    if not node.level:
        return node.module or ""
    parts = (package or "").split(".")
    return ".".join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))


def _taken_from_contexts(tree: ast.AST, package: str | None) -> list[str]:
    """Each place tree takes today's trust or status from the namespace of a module CONTEXTS names, which bound them for
    itself: `from <it> import trust`, or `<it>.trust` read off a name it was imported as. The imports of that module
    alone would not show them (review/code/B21.md 1)."""
    contexts = _contexts_modules()
    bound: set[str] = set()
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = _base(node, package)
            for alias in node.names:
                if base in contexts and alias.name in TODAYS_CONTEXTS:
                    found.append(f"{base}.{alias.name}")
                if f"{base}.{alias.name}" in contexts:
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            bound |= {alias.asname for alias in node.names if alias.name in contexts and alias.asname}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in TODAYS_CONTEXTS and getattr(node.value, "id", "") in bound:
            found.append(f"{node.value.id}.{node.attr}")  # type: ignore[attr-defined]
    return found


def _contexts_made(source: str, path: Path) -> list[str]:
    """What a file of the new engine at path, whose text is source, does that only the modules CONTEXTS names may: it
    makes a TLS context, gives one roots or sets its checks (_makes_a_context), or imports one of today's modules that
    does (TODAYS_CONTEXTS), relative imports resolved, or takes one from a module CONTEXTS names
    (_taken_from_contexts)."""
    tree = ast.parse(source)
    todays = {module for module in _imported(tree, _package(path), every=True) if _within(module, TODAYS_CONTEXTS)}
    return _makes_a_context(tree) + sorted(todays) + _taken_from_contexts(tree, _package(path))


def _todays_modules_whole(source: str, path: Path) -> list[str]:
    """Each of today's modules that make a context (TODAYS_CONTEXTS) that a file at path imports whole, as a module:
    `import claude_vm.trust`, `from .. import trust`, `from ..status import engine`, import_module of one. A name taken
    from one (`from ..trust import anchored_context`) is not."""
    tree, package = ast.parse(source), _package(path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if _within(alias.name, TODAYS_CONTEXTS)]
        elif isinstance(node, ast.ImportFrom):
            base = _base(node, package)
            found += [
                f"{base}.{alias.name}"
                for alias in node.names
                if _exists(f"{base}.{alias.name}") and _within(f"{base}.{alias.name}", TODAYS_CONTEXTS)
            ]
            if not node.names or any(alias.name == "*" for alias in node.names):
                found += [base] if _within(base, TODAYS_CONTEXTS) else []
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            value = node.args[0].value
            if name in ("import_module", "__import__") and isinstance(value, str) and _within(value, TODAYS_CONTEXTS):
                found.append(value)
    return found


def test_a_module_contexts_names_takes_names_of_today_s_modules_never_a_module_whole() -> None:
    """So that no module reaches today's trust, or the rest of it, through a CONTEXTS module's namespace: pin.py binds
    anchored_context alone (review/code/B21.md 1, whose rogue got the interpreter's own store with no name check)."""
    whole = {rel: _todays_modules_whole((REPO / rel).read_text(), REPO / rel) for rel in CONTEXTS}
    assert {rel: found for rel, found in whole.items() if found} == {}


@pytest.mark.parametrize(
    ("source", "whole"),
    [
        ("from .. import trust", True),
        ("import claude_vm.trust", True),
        ("import claude_vm.trust as today", True),
        ("from ..status import engine", True),
        ("from ..trust import *", True),
        ("importlib.import_module('claude_vm.trust')", True),
        ("from ..trust import anchored_context", False),
        ("from ..status.engine import read", False),
        ("from ..vendors import trust as vendors_trust", False),
    ],
)
def test_the_whole_module_scan_sees_each_way(source, whole) -> None:
    assert bool(_todays_modules_whole(source, REPO / "lib" / "claude_vm" / "pve" / "probe.py")) is whole


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
        ("from ..pve.pin import trust as today", True),
        ("from claude_vm.pve.pin import status", True),
        ("from ..vendors.trust import trust", True),
        ("from ..pve import pin\npin.trust._context(None, None, None)", True),
        ("import claude_vm.pve.pin as held\nheld.trust", True),
        ("from ..pve.pin import kept", False),
        ("from ..pve import pin\npin.kept(anchor)", False),
    ],
)
def test_an_import_of_todays_contexts_is_found_relative_or_whole(source, imports) -> None:
    path = REPO / "lib" / "claude_vm" / "pve" / "probe.py"
    assert _package(path) == "claude_vm.pve"
    assert bool(_contexts_made(source, path)) is imports


def test_a_module_of_vendors_that_has_today_s_trust_make_its_context_is_found() -> None:
    """The review's break (review/code/B20.md 1): a context of the interpreter's store with no name check, made by
    today's trust.py for a module of vendors/ that writes no name of MAKERS or CHECKS."""
    rogue = (
        "from .. import trust as today\n\n\n"
        "def context():\n"
        "    return today._context(None, None, None, check_hostname=False)\n"
    )
    assert _contexts_made(rogue, REPO / "lib" / "claude_vm" / "vendors" / "rogue.py") == ["claude_vm.trust"]
    # The same, today's trust taken from pin.py's namespace (review/code/B21.md 1), which pin.py no longer binds.
    through = rogue.replace("from .. import trust as today", "from ..pve.pin import trust as today")
    assert _contexts_made(through, REPO / "lib" / "claude_vm" / "vendors" / "rogue.py") == ["claude_vm.pve.pin.trust"]
    from claude_vm.pve import pin

    assert not hasattr(pin, "trust") and pin.anchored_context.__module__ == "claude_vm.trust"


def test_only_the_modules_contexts_names_make_a_tls_context() -> None:
    """The vendors' context is trust.py's alone, and the Proxmox client's pin.py's: no other module of the new engine
    makes one, gives one roots or loosens one's checks, or imports one of today's that does, and hands it to the
    transport, which takes any context."""
    making = {_rel(path): _contexts_made(path.read_text(), path) for path in _new_engine()}
    assert {rel for rel, found in making.items() if found} == set(CONTEXTS), (
        "a module that makes a TLS context is named in CONTEXTS, with why: "
        f"{ {rel: found for rel, found in making.items() if found and rel not in CONTEXTS} }"
    )
    assert all(reason.strip() for reason in CONTEXTS.values())


# What a Pin keeps its context under (lib/claude_vm/pve/pin.py): a private name, and no field of the dataclass.
PINNED = "_context"


def _context_reads(source: str) -> list[str]:
    """Where source reads a Pin's context: the attribute PINNED off anything but self, which is a class's own
    (the transport keeps its context as self._context), the name as a string (getattr, vars(...)[...], __dict__,
    attrgetter), or a class pattern's keyword (case Pin(_context=...)). A Pin keeps it as no field, so no astuple,
    asdict, repr or positional pattern of one holds it, and a run's own context, or anything else named context, is
    none of these. Past the scan, a name built at run time ("_" + "context"): a reviewer looks for that."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == PINNED:
            if not (isinstance(node.value, ast.Name) and node.value.id == "self"):
                found.append(f"line {node.lineno}: {ast.unparse(node)}")
        elif isinstance(node, ast.Constant) and node.value == PINNED:
            found.append(f"line {node.lineno}: {node.value!r}")
        elif isinstance(node, ast.MatchClass) and PINNED in node.kwd_attrs:
            found.append(f"line {node.lineno}: a class pattern of {PINNED}")
    return found


def test_a_pin_s_context_is_read_by_pin_py_alone() -> None:
    """review/code/B22.md 3, and B26.md 4: where a fingerprint is recorded, a Pin's context checks no name of itself,
    whatever the anchor: pin.transport hands the fingerprint's check beside it. A connection made from that context
    anywhere else would check neither the name nor the fingerprint - with a CA's anchor, any certificate that CA signed
    - so no module of the new engine but pin.py reads a Pin's _context."""
    pin_py = REPO / "lib" / "claude_vm" / "pve" / "pin.py"
    reading = {_rel(path): _context_reads(path.read_text()) for path in _new_engine() if path != pin_py}
    assert {rel: found for rel, found in reading.items() if found} == {}


@pytest.mark.parametrize(
    ("source", "reads"),
    [
        ("pinned._context", True),
        ("self._pin._context.wrap_socket(raw)", True),
        ("from ..pve import pin\npin.kept(anchor)._context", True),
        ("getattr(pinned, '_context')", True),
        ("vars(pinned)['_context']", True),
        ("pinned.__dict__['_context']", True),
        ("operator.attrgetter('_context')(pinned)", True),
        ("match pinned:\n    case Pin(_context=made):\n        pass", True),
        # review/code/B26.md 4: what the scan of a context named context flagged with no Pin in sight.
        ("self.context.stopping.is_set()", False),
        ("record = {'context': 'deploy'}", False),
        ("error.context", False),
        ("self._context = context", False),
        ("self._context.wrap_socket(raw, server_hostname=host)", False),
        ("from . import trust\ntrust.context(None)", False),
        ("from ..pve import pin\npin.context(anchor)", False),
        ("Transport(url, context=made, user_agent=AGENT)", False),
    ],
)
def test_the_scan_of_a_context_read_sees_each_way(source, reads) -> None:
    assert bool(_context_reads(source)) is reads


def test_a_pin_keeps_its_context_as_no_field_that_astuple_asdict_repr_or_a_match_reads(pki) -> None:
    """review/code/B26.md 4: the readings of a Pin the scan cannot see by a name - dataclasses.astuple, asdict, its
    repr and a positional class pattern - hold no context, since a Pin keeps it as no field."""
    import dataclasses

    from claude_vm.pve import pin

    pinned = pin.kept(pki["ca"])
    assert isinstance(pinned._context, ssl.SSLContext)
    assert dataclasses.astuple(pinned) == (True, "")
    assert dataclasses.asdict(pinned) == {"anchored": True, "fingerprint": ""}
    assert [field.name for field in dataclasses.fields(pinned)] == ["anchored", "fingerprint"]
    assert "SSLContext" not in repr(pinned)
    assert pin.Pin.__match_args__ == ("anchored", "fingerprint")
    with pytest.raises(TypeError):
        # Three positional patterns, as a Pin with its context as a field would take: a Pin takes two.
        match pinned:
            case pin.Pin(_, _, _):
                pass
    matched = None
    match pinned:
        case pin.Pin(anchored, fingerprint):
            matched = (anchored, fingerprint)
    assert matched == (True, "")
    assert pinned != pin.kept(pki["ca"])


def test_certifi_is_imported_by_vendors_trust_alone() -> None:
    importing = [
        _rel(path)
        for path in _new_engine()
        if any(name == "certifi" or name.startswith("certifi.") for name in _imports(ast.parse(path.read_text())))
    ]
    assert importing == [_rel(TRUST)]
