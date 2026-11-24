"""The API's certificate, worked out with no credential (lib/claude_vm/engine/certificate.py;
docs/architecture/engine.md §16.4).

Each Proxmox host here is a local TLS server tests/unit/test_proxmox_trust.py already builds (_serve), serving a node's
certificate under a CA made as Proxmox makes one (tests/unit/pki.py), and recording the headers of every request it is
sent: no probe carries a credential. Root SSH is a stand-in: a Read of the test's own certificates, or the host as a
stand-in machine of tests/unit/aw_machine.py's, reached as root through B3's runner, whose command reads files of the
test's own. Every case of tests/unit/test_init_check.py on the certificate is carried, as the new engine has it: a
certificate root SSH reads is kept with nobody there, a host's own under another name is kept as the node's own and
reached by its fingerprint, and where nothing on the host vouches for what the address presents, only a person's yes or
--certificate keeps it. The host's own under another name is the node's own certificate, byte for byte, or one the
host's CA signed, and nothing else: never what an intermediate of a custom certificate's chain issued, at a terminal or
with nobody there. A --certificate given is compared where what checking uses now verifies too, and a yes is True and
nothing else. The rules with a test of their own: nothing in engine/ builds a TLS context for a Proxmox host but through
pve/pin.py, and nothing in the new subpackages reads today's trust.py's _store_files.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import os
import socket
from pathlib import Path

import pytest
import yaml
from aw_machine import Machine, filed, machine, ssh_stand_in, target
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from harness import REPO
from pki import proxmox_pki
from test_aw_transport import _new_engine, _rel
from test_aw_vendor_trust import _makes_a_context
from test_proxmox_trust import A_KEY, _Proxmox, _serve, _Silent

from claude_vm import trust
from claude_vm.engine import certificate
from claude_vm.engine.certificate import Host, Kept, NoRootSsh, Question, Read, Refusal, work_out
from claude_vm.fleet import hosts, paths
from claude_vm.hosts.model import Request
from claude_vm.pve import pin
from claude_vm.runs import job as jobs
from claude_vm.schema.printer import said
from claude_vm.schema.words import Line
from claude_vm.vendors.transport import Untrusted

ENGINE = REPO / "lib" / "claude_vm" / "engine"
CERTIFICATE = ENGINE / "certificate.py"
FIELDS = {"token_user": "aw@pve", "token_id": "aw"}
SETTINGS = {
    "proxmox_node": "pve",
    "proxmox_storage": "local-lvm",
    "proxmox_bridge": "vmbr0",
    "proxmox_template_vmid": 9000,
}
# What no sentence of the step says: a way to check less, or to take the anchor out.
LOOSER = ("validate", "false", "unchecked", "not checked", "delete", "turn", "off")


# ---------------------------------------------------------------- the servers and the stand-ins ----


class _Seen:
    """The headers of every request a server of the test's was sent."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []


@pytest.fixture
def serve():
    """start(pki, handler) serves pki's node certificate with handler (Proxmox's answers to no token by default),
    recording every request's headers, and gives the server's port; seen holds them."""
    servers = []
    seen = _Seen()

    def start(pki: dict, handler: type = _Proxmox) -> int:
        class Recording(handler):  # type: ignore[valid-type, misc]
            def do_GET(self):  # noqa: N802 - http.server's name
                seen.headers.append({key.lower(): value for key, value in self.headers.items()})
                super().do_GET()

            def do_HEAD(self):  # noqa: N802 - http.server's name
                seen.headers.append({key.lower(): value for key, value in self.headers.items()})
                super().do_HEAD()

        server, _url = _serve(pki, Recording)
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
    # No request any server here was sent carried a credential: no token, no ticket, no password.
    for headers in seen.headers:
        assert not {"authorization", "cookie", "csrfpreventiontoken"} & set(headers), headers


def _host(port: int, **kept) -> Host:
    return Host(name="lab", address="127.0.0.1", api_port=port, **kept)


def _read(ca: str = "", node: str = ""):
    """Root SSH that reads ca and node; called counts its calls."""

    async def read() -> Read:
        read.called += 1  # type: ignore[attr-defined]
        return Read(ca=ca, node=node)

    read.called = 0  # type: ignore[attr-defined]
    return read


async def _unanswered() -> Read:
    raise NoRootSsh("ssh: connect to host 127.0.0.1 port 22: Connection refused")


def _asking(answer: object):
    """A person at the terminal who answers every question with answer; asked holds the questions."""

    async def ask(question: Question) -> object:
        ask.asked.append(question)  # type: ignore[attr-defined]
        return answer

    ask.asked = []  # type: ignore[attr-defined]
    return ask


def _worked(host: Host, given: str = "", root_ssh=None, ask=None) -> Kept | Refusal:
    outcome = asyncio.run(work_out(host, given, root_ssh, ask))
    for words in LOOSER:
        assert words not in outcome.said.lower(), (words, outcome.said)
    return outcome


def _kept(outcome) -> Kept:
    assert isinstance(outcome, Kept), outcome
    return outcome


def _refused(outcome) -> Refusal:
    assert isinstance(outcome, Refusal), outcome
    return outcome


def _line(**values) -> str:
    return said("{line}", {"line": Line("init", {"host": "lab", **values})})


def _keep(outcome: Kept, name: str = "lab", port: int = 8006) -> hosts.Reached:
    """What outcome keeps, written through the host's one writer as init writes a host, and read back as a run reaches
    it."""
    layout = hosts.Layout(paths.home(REPO))
    state = layout.state(name)
    given = {"address": "127.0.0.1", "api_port": port, **FIELDS}
    hosts.write(layout, name, state.digest, change=lambda raw: raw.update(given), anchor=outcome.anchor)
    (layout.hosts_dir / name / "settings.yml").write_text(yaml.safe_dump(SETTINGS))
    return hosts.reach(layout, Request(host=name))


def _used(reached: hosts.Reached, port: int, tls_ca_file: str | None = None) -> int:
    """The client's own connection to the host, as reached, with no credential: the status of GET /version, which
    Proxmox answers 401 to no token once the certificate verified."""
    kept = pin.kept(reached.anchor, reached.fields.fingerprint, tls_ca_file)
    transport = pin.transport(f"https://127.0.0.1:{port}/api2/json", kept)
    try:
        return transport.request("GET", "version").status
    finally:
        transport.close()


def _issued(name: str, issuer: tuple | None = None, *, can_sign: bool = False) -> tuple:
    """(certificate, key): a certificate for the DNS name, signed by issuer's (certificate, key) or by its own key, with
    what a strict check asks of each. One that can sign is a CA, as a public CA's root and intermediate are, or a
    server's own certificate made so. Fake, made per test; an EC key, as quick to make as a test wants."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    signer, signer_key = issuer if issuer is not None else (None, key)
    now = datetime.datetime.now(datetime.UTC)
    unused = ("content_commitment", "key_encipherment", "data_encipherment", "key_agreement", "encipher_only")
    usage = dict.fromkeys((*unused, "decipher_only"), False)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject if signer is None else signer.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=can_sign, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, key_cert_sign=can_sign, crl_sign=can_sign, **usage), critical=True
        )
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer_key.public_key()), critical=False)
        .sign(signer_key, hashes.SHA256())
    )
    return certificate, key


def _pem(*certificates) -> str:
    return "".join(one.public_bytes(serialization.Encoding.PEM).decode() for one in certificates)


def _served(directory: Path, chain: list, key) -> dict:
    """What serve() serves: chain's certificates, the server's own first, and its key, as files of the test's own."""
    directory.mkdir(parents=True, exist_ok=True)
    cert, key_file = directory / "cert.pem", directory / "key.pem"
    cert.write_text(_pem(*chain))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return {"cert": cert, "key": key_file}


# ---------------------------------------------------------------- what checking uses now ----


def test_a_kept_anchor_that_verifies_changes_nothing_and_reads_nothing_over_root_ssh(home, serve, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    read = _read(pki["ca"], pki["node"])
    kept = _kept(_worked(_host(port, anchor=pki["ca"], trust_kind="ca"), root_ssh=read))
    assert kept.changed is False and kept.anchor == hosts.Anchor(pki["ca"], "ca", "")
    assert kept.said == "Proxmox's certificate verifies against fleet/hosts/lab/proxmox-trust.pem: every run checks it"
    fingerprint = trust.pem_fingerprint(pki["node"])
    kept = _kept(_worked(_host(port, anchor=pki["node"], trust_kind="node", fingerprint=fingerprint), root_ssh=read))
    assert kept.changed is False and kept.anchor.fingerprint == fingerprint
    assert read.called == 0
    # A node's anchor whose recorded fingerprint is another's does not hold: the client would refuse it.
    other = ":".join(["AB"] * 32)
    refused = _refused(_worked(_host(port, anchor=pki["node"], trust_kind="node", fingerprint=other), root_ssh=read))
    assert refused.said.startswith("Proxmox's certificate no longer verifies against fleet/hosts/lab/proxmox-trust.pem")
    assert read.called == 1


def test_a_certificate_the_computer_s_file_verifies_keeps_no_anchor_and_its_client_verifies(home, serve, tmp_path):
    # A certificate from a public CA, as ACME's: here the CA, in a stand-in for the computer's file, as tls_ca_file.
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    computer = tmp_path / "company-roots.pem"
    computer.write_text(pki["ca"])
    read = _read(pki["ca"], pki["node"])
    kept = _kept(_worked(_host(port, tls_ca_file=str(computer)), root_ssh=read))
    assert kept.anchor is None and kept.changed is False and read.called == 0
    assert "this computer's certificates" in kept.said
    reached = _keep(kept, port=port)
    assert reached.anchor is None and reached.fields.trust_kind is None
    assert not (hosts.Layout(paths.home(REPO)).hosts_dir / "lab" / hosts.ANCHOR).exists()
    assert _used(reached, port, str(computer)) == 401


def test_the_interpreter_s_store_and_its_variables_verify_nothing(home, serve, tmp_path, monkeypatch) -> None:
    # SSL_CERT_FILE and REQUESTS_CA_BUNDLE reach trust.py's own idea of this machine's store, never the new engine's:
    # the computer's certificates are certifi's list and tls_ca_file's alone.
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(pki["ca"])
    for variable in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.setenv(variable, str(bundle))
    refused = _refused(_worked(_host(port)))
    assert "nothing on the host vouches for" in refused.said


def test_a_tls_ca_file_that_cannot_be_used_is_said_as_the_client_says_it(home, serve, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    refused = _refused(_worked(_host(port, tls_ca_file=str(tmp_path / "none.pem")), root_ssh=_read(pki["ca"])))
    assert "tls_ca_file" in refused.said and "is not there" in refused.said


# ---------------------------------------------------------------- read over root SSH ----


def test_proxmox_ve_8_s_ca_is_refused_and_the_node_s_own_certificate_kept_with_nobody_there(home, serve, tmp_path):
    # Every Python the project runs on verifies strictly: a CA with no keyUsage is refused, and the node's own
    # certificate verifies. Root SSH vouches for it, so it is kept with nobody there, held to its fingerprint.
    pki = proxmox_pki(tmp_path / "pki")
    port = serve(pki)
    kept = _kept(_worked(_host(port), root_ssh=_read(pki["ca"], pki["node"])))
    fingerprint = trust.pem_fingerprint(pki["node"])
    assert kept.anchor == hosts.Anchor(pki["node"], "node", fingerprint) and kept.changed
    assert kept.said == (
        f"Proxmox's certificate verifies against the node's own certificate (SHA-256 {fingerprint}), read over root"
        " SSH and kept in fleet/hosts/lab/proxmox-trust.pem, held to its fingerprint: every run checks it"
    )
    reached = _keep(kept, port=port)
    assert (reached.anchor, reached.fields.trust_kind, reached.fields.fingerprint) == (pki["node"], "node", fingerprint)
    assert _used(reached, port) == 401
    # Checked from then on: the next run finds it verifies, and reads nothing over root SSH.
    read = _read(pki["ca"], pki["node"])
    again = _kept(_worked(Host.of("lab", reached.fields, reached.anchor), root_ssh=read))
    assert again.changed is False and read.called == 0


def test_a_ca_with_key_usage_is_the_one_kept_and_its_renewals_verify(home, serve, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    kept = _kept(_worked(_host(port), root_ssh=_read(pki["ca"], pki["node"])))
    assert kept.anchor == hosts.Anchor(pki["ca"], "ca", "")
    assert f"verifies against its CA (SHA-256 {trust.pem_fingerprint(pki['ca'])}), read over root SSH" in kept.said
    reached = _keep(kept, port=port)
    assert reached.fields.fingerprint == "" and _used(reached, port) == 401


def test_another_host_s_anchor_is_kept_in_that_host_s_own_folder(home, serve, tmp_path) -> None:
    lab, nuc = proxmox_pki(tmp_path / "lab", key_usage=True), proxmox_pki(tmp_path / "nuc", key_usage=True)
    lab_port, nuc_port = serve(lab), serve(nuc)
    _keep(_kept(_worked(_host(lab_port), root_ssh=_read(lab["ca"], lab["node"]))), "lab", lab_port)
    nuc_host = Host(name="nuc", address="127.0.0.1", api_port=nuc_port)
    kept = _kept(_worked(nuc_host, root_ssh=_read(nuc["ca"], nuc["node"])))
    assert "kept in fleet/hosts/nuc/proxmox-trust.pem" in kept.said
    reached = _keep(kept, "nuc", nuc_port)
    assert reached.anchor == nuc["ca"]
    assert hosts.reach(hosts.Layout(paths.home(REPO)), Request(host="lab")).anchor == lab["ca"]


def test_a_custom_certificate_is_kept_without_its_chain_and_a_key_beside_one_never(home, serve, tmp_path) -> None:
    # pveproxy serves pveproxy-ssl.pem where there is one, with its chain, and a key may sit in the same file: the
    # node's own certificate alone is kept, since each certificate of an anchor is an anchor of its own.
    pki, other = proxmox_pki(tmp_path / "pki"), proxmox_pki(tmp_path / "other")
    port = serve(pki)
    kept = _kept(_worked(_host(port), root_ssh=_read(other["ca"], pki["node"] + other["ca"] + A_KEY)))
    assert kept.anchor is not None and kept.anchor.pem == pki["node"]
    _keep(kept, port=port)
    folder = hosts.Layout(paths.home(REPO)).hosts_dir / "lab"
    assert "PRIVATE" not in (folder / hosts.ANCHOR).read_text()


def test_the_host_s_own_certificate_under_another_name_is_kept_and_reached_by_its_fingerprint(home, serve, tmp_path):
    # Its certificate names another address than the one the host is reached at (a tailnet's, a forwarded port's):
    # neither its CA nor the node's certificate verifies with the name checked, and what it presents is the host's own,
    # as root SSH reads it. Kept as the node's own, held to its fingerprint and not to a name, the client reaches it.
    pki = proxmox_pki(tmp_path / "pki", key_usage=True, address="127.0.0.2")
    port = serve(pki)
    kept = _kept(_worked(_host(port), root_ssh=_read(pki["ca"], pki["node"])))
    fingerprint = trust.pem_fingerprint(pki["node"])
    assert kept.anchor == hosts.Anchor(pki["node"], "node", fingerprint)
    assert f"is the host's own (SHA-256 {fingerprint}), as root SSH reads it, and does not name 127.0.0.1" in kept.said
    assert "held to its fingerprint and not to a name" in kept.said
    reached = _keep(kept, port=port)
    assert _used(reached, port) == 401
    # The same anchor with no fingerprint beside it checks the name, and is refused before anything is sent.
    transport = pin.transport(f"https://127.0.0.1:{port}/api2/json", pin.kept(reached.anchor, ""))
    try:
        with pytest.raises(Untrusted):
            transport.request("GET", "version")
    finally:
        transport.close()


@pytest.mark.parametrize("handler", [_Proxmox, _Silent], ids=["answers", "says-nothing"])
def test_what_answers_with_none_of_the_host_s_certificates_holds_the_token(home, serve, tmp_path, handler) -> None:
    # Root SSH reads the host's certificates, while something else answers on the API's port with one of its own -
    # whether it answers a request with no token or waits for one that carries it: its certificate alone decides.
    host, impostor = proxmox_pki(tmp_path / "host", key_usage=True), proxmox_pki(tmp_path / "impostor", key_usage=True)
    port = serve(impostor, handler)
    refused = _refused(_worked(_host(port), root_ssh=_read(host["ca"], host["node"]), ask=_asking(True)))
    presented = trust.pem_fingerprint(impostor["node"])
    assert refused.token_held is True
    assert refused.said.startswith(
        f"https://127.0.0.1:{port} presents a certificate (SHA-256 {presented}) that is none of the host's, as root SSH"
        " to 127.0.0.1 reads them, so something other than the Proxmox host answers there, and nothing was sent with"
        " the token. Check the address in fleet/hosts/lab/host.yml"
    )


def test_nothing_presented_where_root_ssh_read_the_host_s_holds_the_token(home, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        closed = free.getsockname()[1]
    refused = _refused(_worked(_host(closed), root_ssh=_read(pki["ca"], pki["node"])))
    assert refused.token_held is True
    assert refused.said.startswith(f"https://127.0.0.1:{closed} presented no certificate to read (")
    assert "so nothing shows that the Proxmox host answers there, and nothing was sent with the token" in refused.said


# ---------------------------------------------------------------- the host's own under another name, and no more ----
# The owner's answer of 2026-11-23 (review 20): the node's own certificate, byte for byte, or one the host's own CA
# signed, both kept with no question; nothing else, an intermediate of a custom certificate's chain above all.


@pytest.mark.parametrize("there", [True, False], ids=["at-a-terminal", "nobody-there"])
def test_what_the_custom_certificate_s_intermediate_issued_another_is_none_of_the_host_s(
    home, serve, tmp_path, there
) -> None:
    # Proxmox's ACME writes pveproxy-ssl.pem with its chain, and init is given an address that certificate does not
    # name (a tailnet's, an IP address, a forwarded port's). What answers there with any certificate the same
    # intermediate issued, for any name, is none of the host's: refused with the token held, nobody asked.
    root = _issued("Public Root", can_sign=True)
    intermediate = _issued("Public Intermediate", root, can_sign=True)
    leaf, impostor = _issued("pve.example.org", intermediate), _issued("attacker.example.net", intermediate)
    proxmox = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(_served(tmp_path / "impostor", [impostor[0], intermediate[0]], impostor[1]))
    ask = _asking(True) if there else None
    custom = _pem(leaf[0], intermediate[0])
    refused = _refused(_worked(_host(port), root_ssh=_read(proxmox["ca"], custom), ask=ask))
    assert refused.token_held is True
    presented = trust.pem_fingerprint(_pem(impostor[0]))
    assert f"presents a certificate (SHA-256 {presented}) that is none of the host's" in refused.said
    assert ask is None or ask.asked == []


def test_the_node_s_own_certificate_with_its_chain_is_kept_under_another_name_unasked(home, serve, tmp_path) -> None:
    # The host's own custom certificate, byte for byte, at an address it does not name: kept as the node's own, the
    # leaf alone, held to its fingerprint, with no question, and the client reaches it.
    root = _issued("Public Root", can_sign=True)
    intermediate = _issued("Public Intermediate", root, can_sign=True)
    leaf = _issued("pve.example.org", intermediate)
    proxmox = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(_served(tmp_path / "host", [leaf[0], intermediate[0]], leaf[1]))
    ask = _asking(False)
    read = _read(proxmox["ca"], _pem(leaf[0], intermediate[0], root[0]))
    kept = _kept(_worked(_host(port), root_ssh=read, ask=ask))
    fingerprint = trust.pem_fingerprint(_pem(leaf[0]))
    assert kept.anchor == hosts.Anchor(_pem(leaf[0]), "node", fingerprint) and ask.asked == []
    assert f"is the host's own (SHA-256 {fingerprint}), as root SSH reads it" in kept.said
    assert _used(_keep(kept, port=port), port) == 401


def test_a_certificate_the_host_s_ca_signed_for_another_name_is_kept_unasked(home, serve, tmp_path) -> None:
    # Another node of the host's cluster, whose certificate the host's CA signed for that node's name: kept as the
    # node's own, held to its fingerprint, with no question, and the client reaches it.
    ca = _issued("Proxmox Virtual Environment", can_sign=True)
    node, other = _issued("pve.lan", ca), _issued("pve2.lan", ca)
    port = serve(_served(tmp_path / "other", [other[0]], other[1]))
    ask = _asking(False)
    kept = _kept(_worked(_host(port), root_ssh=_read(_pem(ca[0]), _pem(node[0])), ask=ask))
    fingerprint = trust.pem_fingerprint(_pem(other[0]))
    assert kept.anchor == hosts.Anchor(_pem(other[0]), "node", fingerprint) and ask.asked == []
    assert _used(_keep(kept, port=port), port) == 401


def test_a_certificate_the_node_s_own_certificate_signed_is_none_of_the_host_s(home, serve, tmp_path) -> None:
    # A custom certificate made so that it can sign is no CA of the host's: what it signed for another name is neither
    # the node's own certificate nor one the host's CA signed.
    custom = _issued("pve.example.org", can_sign=True)
    signed = _issued("attacker.example.net", custom)
    proxmox = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(_served(tmp_path / "signed", [signed[0]], signed[1]))
    ask = _asking(True)
    refused = _refused(_worked(_host(port), root_ssh=_read(proxmox["ca"], _pem(custom[0])), ask=ask))
    assert refused.token_held is True and ask.asked == []


# ---------------------------------------------------------------- what only a person can vouch for ----


@pytest.mark.parametrize("root_ssh", [None, _unanswered, _read()], ids=["not-tried", "unanswered", "nothing-read"])
def test_with_nothing_on_the_host_to_vouch_a_yes_keeps_it_and_a_no_does_not(home, serve, tmp_path, root_ssh) -> None:
    pki = proxmox_pki(tmp_path / "pki")
    port = serve(pki)
    fingerprint = trust.pem_fingerprint(pki["node"])
    no = _asking(False)
    refused = _refused(_worked(_host(port), root_ssh=root_ssh, ask=no))
    assert refused.said == f"SHA-256 {fingerprint} was not trusted: nothing was kept, and nothing sent with the token"
    question = no.asked[0]
    assert question.prompt == "Trust it? y or n" and f"  SHA-256 {fingerprint}" in question.lines
    assert "nothing on the host vouches for" in question.lines[0]
    yes = _asking(True)
    kept = _kept(_worked(_host(port), root_ssh=root_ssh, ask=yes))
    assert kept.anchor == hosts.Anchor(pki["node"], "node", fingerprint) and len(yes.asked) == 1
    assert "as you vouched for it, held to its fingerprint" in kept.said
    assert _used(_keep(kept, port=port), port) == 401


def test_with_nobody_there_only_the_fingerprint_given_keeps_it(home, serve, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki")
    port = serve(pki)
    fingerprint = trust.pem_fingerprint(pki["node"])
    # --no-prompt, no --certificate: a refusal that names --certificate, with the fingerprint to compare.
    refused = _refused(_worked(_host(port), root_ssh=_unanswered))
    assert refused.token_held is False
    assert (
        f"presents a certificate (SHA-256 {fingerprint}) that nothing on the host vouches for (root SSH to 127.0.0.1"
        " did not answer: ssh: connect to host 127.0.0.1 port 22: Connection refused): only you can" in refused.said
    )
    assert refused.said.endswith(f"then {_line(certificate=fingerprint)} keeps it")
    # Another fingerprint keeps nothing; its own, as typed in lower case, keeps it, and no one is asked.
    wrong = ":".join(["AB"] * 32)
    refused = _refused(_worked(_host(port), wrong, _unanswered, _asking(True)))
    assert refused.said.startswith(f"--certificate {wrong} is not the certificate it presents (SHA-256 {fingerprint})")
    ask = _asking(False)
    kept = _kept(_worked(_host(port), fingerprint.lower(), _unanswered, ask))
    assert kept.anchor is not None and kept.anchor.fingerprint == fingerprint and ask.asked == []


def test_a_fingerprint_given_that_is_not_the_one_root_ssh_reads_keeps_nothing(home, serve, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    wrong = ":".join(["AB"] * 32)
    refused = _refused(_worked(_host(port), wrong, _read(pki["ca"], pki["node"])))
    ca = trust.pem_fingerprint(pki["ca"])
    assert refused.said == (
        f"--certificate {wrong} is not its CA, read over root SSH (SHA-256 {ca}): nothing was kept, and nothing sent"
        " with the token"
    )
    assert _kept(_worked(_host(port), ca.lower(), _read(pki["ca"], pki["node"]))).anchor.kind == "ca"


def test_a_fingerprint_given_where_the_kept_anchor_verifies_is_compared_with_the_anchor_s_own(
    home, serve, tmp_path
) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    read = _read(pki["ca"], pki["node"])
    ca, node = trust.pem_fingerprint(pki["ca"]), trust.pem_fingerprint(pki["node"])
    wrong = ":".join(["AB"] * 32)
    path = "fleet/hosts/lab/proxmox-trust.pem"
    as_ca = _host(port, anchor=pki["ca"], trust_kind="ca")
    refused = _refused(_worked(as_ca, wrong, read))
    assert refused.said == (
        f"--certificate {wrong} is not its CA, the anchor kept in {path} (SHA-256 {ca}), against which Proxmox's"
        " certificate verifies: nothing was changed, and nothing sent with the token"
    )
    kept = _kept(_worked(as_ca, ca.lower(), read))
    assert kept.changed is False and kept.anchor == hosts.Anchor(pki["ca"], "ca", "")
    assert kept.said == (
        f"Proxmox's certificate verifies against {path}, and --certificate matched its CA kept there (SHA-256 {ca}):"
        " every run checks it"
    )
    # A node's anchor: the fingerprint recorded beside it is its own.
    as_node = _host(port, anchor=pki["node"], trust_kind="node", fingerprint=node)
    refused = _refused(_worked(as_node, ca, read))
    assert refused.said.startswith(f"--certificate {ca} is not the node's own certificate, the anchor kept in {path}")
    assert f"(SHA-256 {node})" in refused.said
    kept = _kept(_worked(as_node, node, read))
    assert kept.changed is False and kept.anchor == hosts.Anchor(pki["node"], "node", node)
    assert f"--certificate matched the node's own certificate kept there (SHA-256 {node})" in kept.said
    assert read.called == 0


def test_a_fingerprint_given_where_the_computer_s_certificates_verify_is_compared_with_what_is_presented(
    home, serve, tmp_path
) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    port = serve(pki)
    computer = tmp_path / "company-roots.pem"
    computer.write_text(pki["ca"])
    read = _read(pki["ca"], pki["node"])
    presented, wrong = trust.pem_fingerprint(pki["node"]), ":".join(["AB"] * 32)
    refused = _refused(_worked(_host(port, tls_ca_file=str(computer)), wrong, read))
    assert refused.said == (
        f"--certificate {wrong} is not what https://127.0.0.1:{port} presents (SHA-256 {presented}), which this"
        " computer's certificates verify: nothing was kept, and nothing sent with the token"
    )
    # A match keeps no anchor (the owner's answer of 2026-11-23), and is said.
    kept = _kept(_worked(_host(port, tls_ca_file=str(computer)), presented.lower(), read))
    assert kept.anchor is None and kept.changed is False and read.called == 0
    assert kept.said == (
        "Proxmox's certificate verifies against this computer's certificates (certifi's list, with the file"
        f" tls_ca_file names), and --certificate matched what https://127.0.0.1:{port} presents (SHA-256 {presented}):"
        " the host keeps no anchor, and every run checks it"
    )
    reached = _keep(kept, port=port)
    assert reached.anchor is None and _used(reached, port, str(computer)) == 401


@pytest.mark.parametrize("answer", ["n", 1, None], ids=["the-choice-n", "one", "none"])
def test_a_yes_is_true_and_nothing_else(home, serve, tmp_path, answer) -> None:
    # An ask that hands back the choice made, or a number, is not a yes: nothing is kept.
    pki = proxmox_pki(tmp_path / "pki")
    port = serve(pki)
    ask = _asking(answer)
    refused = _refused(_worked(_host(port), ask=ask))
    fingerprint = trust.pem_fingerprint(pki["node"])
    assert refused.said == f"SHA-256 {fingerprint} was not trusted: nothing was kept, and nothing sent with the token"
    assert len(ask.asked) == 1


# ---------------------------------------------------------------- a certificate that changed ----


def test_a_certificate_that_changed_is_never_put_in_place_unasked(home, serve, tmp_path) -> None:
    first, second = proxmox_pki(tmp_path / "first", key_usage=True), proxmox_pki(tmp_path / "second", key_usage=True)
    port = serve(second)
    old, new = trust.pem_fingerprint(first["ca"]), trust.pem_fingerprint(second["ca"])
    kept_now = _host(port, anchor=first["ca"], trust_kind="ca")
    refused = _refused(_worked(kept_now, root_ssh=_read(second["ca"], second["node"])))
    assert refused.said.startswith(
        f"Proxmox's certificate no longer verifies against fleet/hosts/lab/proxmox-trust.pem (SHA-256 {old}), and its"
        f" CA, read over root SSH, is SHA-256 {new}: it is put in its place only on your word, so nothing was sent with"
        f" the token. {_line()} at a terminal asks whether to trust it in its place, and {_line(certificate=new)} puts"
        " it there"
    )
    # Without root SSH, the same: what the address presents, against what is kept.
    presented = trust.pem_fingerprint(second["node"])
    refused = _refused(_worked(kept_now, root_ssh=_unanswered))
    assert f"(SHA-256 {old}), and the certificate it presents is SHA-256 {presented}" in refused.said


def test_a_certificate_that_changed_is_put_in_place_for_its_fingerprint_given(home, serve, tmp_path) -> None:
    first, second = proxmox_pki(tmp_path / "first", key_usage=True), proxmox_pki(tmp_path / "second", key_usage=True)
    port = serve(second)
    old, new = trust.pem_fingerprint(first["ca"]), trust.pem_fingerprint(second["ca"])
    kept = _kept(_worked(_host(port, anchor=first["ca"], trust_kind="ca"), new, _read(second["ca"], second["node"])))
    assert kept.anchor == hosts.Anchor(second["ca"], "ca", "") and kept.changed
    assert kept.said == (
        f"Proxmox's certificate no longer verified against fleet/hosts/lab/proxmox-trust.pem (SHA-256 {old}), and its"
        f" CA, read over root SSH (SHA-256 {new}) was kept in its place: every run checks it"
    )


def test_a_certificate_that_changed_is_put_in_place_on_a_yes_that_showed_both_fingerprints(
    home, serve, tmp_path
) -> None:
    first, second = proxmox_pki(tmp_path / "first", key_usage=True), proxmox_pki(tmp_path / "second", key_usage=True)
    port = serve(second)
    old, new = trust.pem_fingerprint(first["ca"]), trust.pem_fingerprint(second["ca"])
    _keep(Kept(hosts.Anchor(first["ca"], "ca"), True, ""), port=port)
    reached = hosts.reach(hosts.Layout(paths.home(REPO)), Request(host="lab"))
    ask = _asking(True)
    kept = _kept(_worked(Host.of("lab", reached.fields, reached.anchor), "", _read(second["ca"], second["node"]), ask))
    (question,) = ask.asked
    assert question.prompt == "Trust it in its place? y or n"
    assert f"  SHA-256 {old}" in question.lines and f"  SHA-256 {new}" in question.lines
    assert any(
        "openssl x509 -noout -fingerprint -sha256 -in /etc/pve/pve-root-ca.pem" in line for line in question.lines
    )
    assert _used(_keep(kept, port=port), port) == 401
    assert hosts.reach(hosts.Layout(paths.home(REPO)), Request(host="lab")).anchor == second["ca"]
    # A no keeps the one kept.
    refused = _refused(
        _worked(_host(port, anchor=first["ca"], trust_kind="ca"), "", _read(second["ca"]), _asking(False))
    )
    assert refused.said.startswith(f"SHA-256 {new} was not trusted")


# ---------------------------------------------------------------- the host's files over root SSH ----


@pytest.fixture
def pve_host(engine_guard, home, tmp_path) -> Machine:
    """The Proxmox host as a stand-in machine of tests/unit/aw_machine.py's, reached as root, the stand-in ssh first on
    this test's own PATH: no engine test writes an ssh of its own (tests/unit/test_aw_machine.py). Its target is
    aw_machine's, its key filed under its identity and checked strictly, where init's is runner.proxmox_host's, which
    takes the host's key at first use: the read is the one command whatever target the runner takes it to, and the
    words of each target are B3's tests' (tests/unit/test_aw_ssh_runner.py)."""
    machines = tmp_path / "machines"
    host = machine(machines, "pve", account="root")
    filed(host)
    engine_guard.first_on_path(ssh_stand_in(machines, [host]))
    return host


@pytest.fixture
def root_ssh(pve_host, tmp_path):
    """read(files, unreachable=False): the host's files read through the runner from pve_host, as a child of a job of
    this process's own; unreachable, from a machine of the same folder that the stand-in ssh has no address for."""
    job = jobs.create(paths.jobs_dir(paths.home(REPO)), "init", "init", "lab", [], {"kind": "terminal"}, True)
    gone = machine(pve_host.directory.parent, "gone", account="root")

    def read(files: dict[str, str], unreachable: bool = False) -> Read:
        at = target(gone if unreachable else pve_host)
        return asyncio.run(certificate.read_over_root_ssh(job, at, dict(os.environ), files))

    yield read
    jobs.drop(job)


def _host_files(tmp_path: Path, **texts: str | bytes) -> dict[str, str]:
    """The host's three files as paths of the test's own, those given written."""
    folder = tmp_path / "etc-pve"
    folder.mkdir(parents=True, exist_ok=True)
    files = {name: str(folder / f"{name}.pem") for name in certificate.HOST_FILES}
    for name, text in texts.items():
        Path(files[name]).write_bytes(text if isinstance(text, bytes) else text.encode())
    return files


def test_the_host_s_files_are_read_over_root_ssh_each_looked_at_first(root_ssh, pve_host, tmp_path) -> None:
    pki = proxmox_pki(tmp_path / "pki", key_usage=True)
    files = _host_files(tmp_path, ca=pki["ca"], node=pki["node"])
    assert root_ssh(files) == Read(ca=pki["ca"], node=pki["node"])
    # The custom certificate pveproxy serves where there is one, in place of the one Proxmox made.
    other = proxmox_pki(tmp_path / "other")
    Path(files["custom"]).write_text(other["node"])
    assert root_ssh(files).node == other["node"]
    # One larger than a certificate file is not read; one that is no text holds no certificate.
    Path(files["ca"]).write_bytes(b"A" * (certificate.MOST + 1))
    Path(files["custom"]).write_bytes(b"\xff\xfe binary")
    assert root_ssh(files) == Read(ca="", node="")
    # None of them there.
    assert root_ssh(_host_files(tmp_path / "empty")) == Read()
    # Each read was one command, run on the host as root.
    assert [call["outcome"] for call in pve_host.calls()] == ["ran"] * 4


def test_root_ssh_that_does_not_answer_is_said(root_ssh, tmp_path) -> None:
    with pytest.raises(NoRootSsh, match="No route to host"):
        root_ssh(_host_files(tmp_path), unreachable=True)


@pytest.mark.parametrize(
    "lines",
    [
        [],
        ["-", "-"],
        ["-", "-", "-", "-"],
        ["+not base64!", "-", "-"],
        ["?", "-", "-"],
        ["+" + "QUFB" * 30000, "-", "-"],
    ],
)
def test_an_answer_that_is_not_one_line_a_file_is_root_ssh_not_answering_as_asked(lines) -> None:
    with pytest.raises(NoRootSsh, match="it did not answer as asked"):
        certificate.parse(lines)


def test_a_file_read_on_the_host_is_an_absolute_path_of_one_line() -> None:
    for files in ({"ca": "etc/pve/ca.pem"}, {"ca": "/etc/pve/\nca.pem"}):
        with pytest.raises(ValueError, match="an absolute path of one line"):
            certificate.read_command(files)
    words = certificate.read_command()
    assert words[:3] == ["sh", "-c", certificate.READ] and words[4:] == list(certificate.HOST_FILES.values())


# ---------------------------------------------------------------- the rules ----


def test_nothing_in_engine_builds_a_tls_context_but_through_pin() -> None:
    """Every context of the engine's for a Proxmox host is pve/pin.py's: no module of engine/ makes one, gives one roots
    or sets its checks, names today's anchored_context or the vendors' context, or reads a Pin's; certificate.py's
    probes are today's trust.py's, which carry no credential, and what it keeps is held to the client's by pin.kept."""
    for path in sorted(ENGINE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        assert _makes_a_context(tree) == [], _rel(path)
        assert not {"anchored_context", "_context", "context", "_presented", "_hello"} & _named(tree), _rel(path)
    tree = ast.parse(CERTIFICATE.read_text())
    from_pin = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and ast.unparse(node.value) == "pin"
    }
    assert from_pin == {"kept"}


def _named(tree: ast.AST) -> set[str]:
    """Every name, attribute and imported name of tree: what its code reaches, never what its docstrings say."""
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    return names | {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}


def test_nothing_in_the_new_engine_reads_today_s_store_files(monkeypatch) -> None:
    """trust.py's _store_files reads REQUESTS_CA_BUNDLE and the interpreter's own store: the new engine's computer's
    certificates are certifi's list and tls_ca_file's alone. Its probe is never handed nothing, which would reach it."""
    for path in _new_engine():
        assert "_store_files" not in _named(ast.parse(path.read_text())), _rel(path)
    reached = []
    monkeypatch.setattr(trust, "_store_files", lambda: reached.append(True) or [])
    for text in ("", " \n"):
        assert asyncio.run(certificate._probed("https://127.0.0.1:9/api2/json", text)) != ""
    assert reached == []


def test_the_step_reads_no_setting_and_no_variable_of_its_own() -> None:
    """What it is given is its caller's: the host, --certificate, root SSH and the person; no os.environ, no getenv."""
    assert not {"environ", "getenv", "environb"} & _named(ast.parse(CERTIFICATE.read_text()))
