"""What makes the Proxmox API's certificate verify, as just init works it out (lib/claude_vm/trust.py): against a local
TLS server that serves a node's certificate alone, as pveproxy does, signed by a CA made the way Proxmox makes one -
with no keyUsage, as every Proxmox VE 8 host's, or with it - and never a real host.

What it rests on (docs/getting-started/proxmox-access.md): Python 3.13 and later verify strictly, refuse a CA without
keyUsage and take the node's own certificate as an anchor of its own; Python 3.12 does neither. Each case is held to
the Python it runs on, since the run's clients - Ansible's uri, requests - verify as that Python does.
"""

from __future__ import annotations

import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from harness import REPO
from pki import proxmox_pki

from claude_vm import trust

STRICT = sys.version_info >= (3, 13)
# A key's PEM block, as a file read from the host could hold beside a certificate: spelt in parts, since the secret
# scanners take the whole header for a key committed.
A_KEY = "-----BEGIN " + "PRIVATE KEY-----\nAAAA\n-----END " + "PRIVATE KEY-----\n"


class _Proxmox(BaseHTTPRequestHandler):
    """Proxmox's answer to a request with no token."""

    def do_GET(self):  # noqa: N802 - http.server's name
        self.send_response(401, "No ticket")
        self.end_headers()

    def do_HEAD(self):  # noqa: N802 - http.server's name
        self.send_response(501, "method 'HEAD' not available")
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 - http.server's signature
        pass


class _Silent(_Proxmox):
    """Something in the host's place that says nothing to a request with no token: it sees which request carries one."""

    def do_GET(self):  # noqa: N802 - http.server's name
        pass

    def do_HEAD(self):  # noqa: N802 - http.server's name
        pass


class _ProxmoxDelaying(_Proxmox):
    """pveproxy as it is: a GET with no token waits three seconds for its 401 (live, 2026-11-15)."""

    def do_GET(self):  # noqa: N802 - http.server's name
        time.sleep(3)
        super().do_GET()


def _serve(pki, handler=_Proxmox):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki["cert"], pki["key"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"https://127.0.0.1:{server.server_address[1]}/api2/json"


@pytest.fixture
def pve_8(tmp_path):
    pki = proxmox_pki(tmp_path / "pve8")
    server, url = _serve(pki)
    yield pki, url
    server.shutdown()


@pytest.fixture
def pve_9(tmp_path):
    pki = proxmox_pki(tmp_path / "pve9", key_usage=True)
    server, url = _serve(pki)
    yield pki, url
    server.shutdown()


def _candidates(pki):
    return [{"name": "store", "pem": None}, {"name": "ca", "pem": pki["ca"]}, {"name": "node", "pem": pki["node"]}]


def test_a_ca_without_key_usage_is_refused_where_python_is_strict_and_the_node_s_certificate_kept(pve_8):
    pki, url = pve_8
    picked = trust.proxmox_trust_pick(_candidates(pki), url)
    assert picked["name"] == ("node" if STRICT else "ca")
    assert picked["pem"] == (pki["node"] if STRICT else pki["ca"])
    assert picked["fingerprint"] == trust.pem_fingerprint(picked["pem"]) and picked["fingerprint"].count(":") == 31
    # This machine's trust store never trusts a Proxmox CA, and where Python is strict, its CA alone is refused.
    whys = {refusal["name"]: refusal["why"] for refusal in picked["refusals"]}
    assert "store" in whys
    if STRICT:
        assert "key usage" in whys["ca"].lower()


def test_a_ca_with_key_usage_is_kept_wherever(pve_9):
    pki, url = pve_9
    picked = trust.proxmox_trust_pick(_candidates(pki), url)
    assert (picked["name"], picked["pem"]) == ("ca", pki["ca"])


def test_a_certificate_that_names_another_address_verifies_against_nothing(tmp_path):
    pki = proxmox_pki(tmp_path / "elsewhere", key_usage=True, address="127.0.0.2")
    server, url = _serve(pki)
    try:
        picked = trust.proxmox_trust_pick(_candidates(pki), url)
    finally:
        server.shutdown()
    assert (picked["name"], picked["pem"]) == ("", "")
    assert [refusal["name"] for refusal in picked["refusals"]] == ["store", "ca", "node"]
    whys = {refusal["name"]: refusal["why"].lower() for refusal in picked["refusals"]}
    assert "mismatch" in whys["ca"]
    # Python 3.12 takes no certificate but a CA's as an anchor: the node's is refused for its chain before its name.
    assert ("mismatch" in whys["node"]) if STRICT else ("issuer" in whys["node"])


def test_nothing_answering_is_said_and_no_candidate_is_kept():
    picked = trust.proxmox_trust_pick([{"name": "ca", "pem": None}], "https://127.0.0.1:9/api2/json", timeout=2)
    assert picked["name"] == "" and picked["refusals"][0]["why"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        A_KEY,
        "not a certificate",
        "x" * 70000,
    ],
)
def test_only_certificates_are_kept_of_what_the_host_s_files_hold(text):
    assert trust.pem_certificates(text) == ""


def test_a_key_beside_a_certificate_is_never_kept(pve_9):
    pki, _ = pve_9
    both = pki["node"] + A_KEY
    assert trust.pem_certificates(both) == pki["node"]


def test_the_fingerprint_is_the_one_openssl_prints(pve_9, tmp_path):
    import subprocess

    pki, _ = pve_9
    (tmp_path / "node.crt").write_text(pki["node"])
    said = subprocess.run(
        ["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(tmp_path / "node.crt")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert said.strip().split("=", 1)[1] == trust.pem_fingerprint(pki["node"])


# ------------------------------------------------------------------ every request reads the same ---


def test_just_status_checks_against_the_trust_file_and_says_a_certificate_that_changed(pve_8, tmp_path):
    from claude_vm import proxmox
    from claude_vm.status import engine

    pki, url = pve_8
    kept = tmp_path / "proxmox-trust.pem"
    kept.write_text(
        trust.proxmox_trust_pick([{"name": "ca", "pem": pki["ca"]}, {"name": "node", "pem": pki["node"]}], url)["pem"]
    )
    fleet = {"url": url, "headers": {}, "timeout": 5, "validate": True}
    api = engine.Proxmox({**fleet, "ca_path": str(kept)})
    try:
        # Proxmox's answer to no token: the certificate verified.
        assert api.get("version")["status"] == 401
    finally:
        api.pool.shutdown()
    kept.write_text(proxmox_pki(tmp_path / "renewed", key_usage=True)["ca"])
    api = engine.Proxmox({**fleet, "ca_path": str(kept)})
    try:
        refused = api.get("version")
    finally:
        api.pool.shutdown()
    assert refused["status"] == -1
    hint = proxmox.proxmox_untrusted_hint(refused["msg"], str(kept))
    assert hint.startswith(f"Proxmox presents a certificate that does not verify against {kept}. If Proxmox renewed it")


@pytest.mark.parametrize(
    ("msg", "trust_file", "said"),
    [
        (
            "Request failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get"
            " local issuer certificate (_ssl.c:1032)>",
            "inventory/proxmox-trust.pem",
            "does not verify against inventory/proxmox-trust.pem. If Proxmox renewed it, the host was reinstalled, or"
            " this machine's Python changed",
        ),
        (
            "certificate verify failed: self-signed certificate in certificate chain",
            "",
            "does not verify against this machine's trust store. If Proxmox renewed it",
        ),
        (
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not"
            " valid for '192.0.2.10'",
            "inventory/proxmox-trust.pem",
            "does not name the address in proxmox_api_host: reach it by a name or address the certificate holds",
        ),
        ("HTTP Error 401: No ticket", "inventory/proxmox-trust.pem", ""),
        ("Request failed: [Errno 61] Connection refused", "", ""),
        ("", "", ""),
    ],
)
def test_a_certificate_that_did_not_verify_is_said_with_what_to_do(msg, trust_file, said):
    from claude_vm import proxmox

    hint = proxmox.proxmox_untrusted_hint(msg, trust_file)
    assert (said in hint) if said else hint == "", hint
    # Never a way out through a run that checks nothing: no turning checking off, no deleting the file it checks by.
    assert "false" not in hint and "delete" not in hint, hint
    if "verify against" in said:
        assert "just init reads the host's own over root SSH and asks before it trusts one in its place" in hint


def test_every_request_to_the_api_checks_as_one_value_says():
    # proxmox_api_validate (inventory defaults) - the setting, or the trust file just init wrote - and the CA beside it;
    # never the setting alone, which a trust file turns on without changing. The proxmox_vm role has its own, wired.
    import re

    paths = [
        *(REPO / "playbooks").rglob("*.yml"),
        *(path for path in (REPO / "roles").rglob("*.yml") if "molecule" not in path.parts),
    ]
    found = 0
    for path in paths:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            match = re.match(r"\s*validate_certs: (.*)$", line)
            if match:
                found += 1
                assert match.group(1) in ('"{{ proxmox_api_validate }}"', '"{{ proxmox_vm_validate_certs }}"'), (
                    f"{path}:{number}"
                )
    assert found > 30
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["proxmox_vm_validate_certs"] == "{{ proxmox_api_validate }}"
    assert defaults["proxmox_vm_ca_path"] == "{{ proxmox_api_ca_path }}"
    assert defaults["proxmox_api_module_defaults"]["validate_certs"] == "{{ proxmox_api_validate }}"
    assert defaults["proxmox_api_module_defaults"]["ca_path"] == "{{ proxmox_api_ca_path }}"


def test_just_status_reads_the_same_and_its_snapshot_sees_the_file_come_and_go(tmp_path):
    from claude_vm.status import settings

    assert settings.API["validate"] == "{{ proxmox_api_validate | bool }}"
    assert settings.API["ca_path"] == "{{ proxmox_api_ca_path | default('', true) }}"
    assert settings.API["trust_file"] == "{{ proxmox_trust_file if proxmox_trust_kept | bool else '' }}"
    # Another host's hosts.yml is given alone: the file beside it is part of the snapshot's key all the same.
    hosts = tmp_path / "hosts" / "nuc" / "hosts.yml"
    hosts.parent.mkdir(parents=True)
    hosts.write_text("all: {}\n")
    assert tmp_path / "hosts" / "nuc" / "proxmox-trust.pem" in set(settings._inventory_files(hosts))


# ------------------------------------------------------------------- either Python's verification ---
# CI runs 3.12 and the environment just install makes 3.13 or later: each candidate is tried with both Python's flags
# forced, so both paths run on either.


@pytest.mark.parametrize("strict", [True, False])
def test_which_candidate_verifies_follows_python_s_strictness_forced(pve_8, strict):
    pki, url = pve_8
    picked = trust.proxmox_trust_pick(_candidates(pki), url, strict=strict)
    # Strict: Proxmox VE 8's CA, with no keyUsage, is refused and the node's own certificate is its own anchor; not:
    # the CA verifies, and the node's certificate alone would not.
    assert picked["name"] == ("node" if strict else "ca"), picked["refusals"]


def test_of_a_node_certificate_file_only_the_node_s_own_is_kept(pve_9):
    # A custom certificate is served with its chain: kept whole, each intermediate would be an anchor of its own.
    pki, url = pve_9
    chained = pki["node"] + pki["ca"]
    assert trust.pem_first(chained) == pki["node"]
    picked = trust.proxmox_trust_pick([{"name": "node", "pem": chained}], url, strict=True)
    assert (picked["name"], picked["pem"]) == ("node", pki["node"])


def test_what_the_address_presents_is_told_from_the_host_s_own(pve_9, tmp_path):
    pki, url = pve_9
    host = [{"name": "store", "pem": None}, {"name": "ca", "pem": pki["ca"]}, {"name": "node", "pem": pki["node"]}]
    presented = trust.proxmox_trust_presented(host, url)
    assert presented == {"fingerprint": trust.pem_fingerprint(pki["node"]), "host": True, "why": ""}
    # Another machine's certificates, as root SSH would read them from a host that is not the one answering.
    other = proxmox_pki(tmp_path / "other", key_usage=True)
    elsewhere = [{"name": "ca", "pem": other["ca"]}, {"name": "node", "pem": other["node"]}]
    assert trust.proxmox_trust_presented(elsewhere, url)["host"] is False
    # The store proves no host: a certificate any public CA signed is anyone's.
    assert trust.proxmox_trust_presented([{"name": "store", "pem": None}], url)["host"] is False


def test_the_host_s_certificate_under_another_name_is_still_the_host_s(tmp_path):
    pki = proxmox_pki(tmp_path / "elsewhere", key_usage=True, address="127.0.0.2")
    server, url = _serve(pki)
    try:
        presented = trust.proxmox_trust_presented([{"name": "ca", "pem": pki["ca"]}], url)
        picked = trust.proxmox_trust_pick(_candidates(pki), url)
    finally:
        server.shutdown()
    assert presented["host"] is True and picked["name"] == ""


def test_what_says_nothing_after_the_handshake_is_known_by_its_certificate(tmp_path):
    host, impostor = proxmox_pki(tmp_path / "host", key_usage=True), proxmox_pki(tmp_path / "impostor", key_usage=True)
    for served, is_host in ((host, True), (impostor, False)):
        server, url = _serve(served, _Silent)
        try:
            presented = trust.proxmox_trust_presented(_candidates(host), url)
            picked = trust.proxmox_trust_pick(_candidates(host), url)
        finally:
            server.shutdown()
        assert presented == {"fingerprint": trust.pem_fingerprint(served["node"]), "host": is_host, "why": ""}
        # Kept is only what the API answers through: a certificate that verifies, then an answer that is HTTP.
        assert picked["name"] == ""


def test_nothing_answering_presents_nothing():
    presented = trust.proxmox_trust_presented([{"name": "ca", "pem": None}], "https://127.0.0.1:9/api2/json", timeout=2)
    assert presented["fingerprint"] == "" and presented["host"] is False and presented["why"]


def test_no_probe_waits_on_the_answer_proxmox_delays(tmp_path):
    # A GET took three seconds a probe, nine for the certificate's step of a live just init.
    pki = proxmox_pki(tmp_path / "pve9", key_usage=True)
    server, url = _serve(pki, _ProxmoxDelaying)
    try:
        started = time.monotonic()
        picked = trust.proxmox_trust_pick(_candidates(pki), url)
        presented = trust.proxmox_trust_presented(_candidates(pki), url)
        held = trust.proxmox_trust_holds(pki["ca"], url)
        took = time.monotonic() - started
    finally:
        server.shutdown()
    assert picked["name"] == "ca" and presented["host"] is True and held["verified"] is True
    assert took < 3, f"the probes took {took:.1f} s"


def test_what_checking_uses_now_is_held_to_whether_it_verifies(pve_9, tmp_path):
    pki, url = pve_9
    assert trust.proxmox_trust_holds(pki["ca"], url) == {
        "verified": True,
        "why": "",
        "fingerprint": trust.pem_fingerprint(pki["ca"]),
    }
    renewed = proxmox_pki(tmp_path / "renewed", key_usage=True)
    held = trust.proxmox_trust_holds(renewed["ca"], url)
    assert held["verified"] is False and held["why"] and held["fingerprint"] == trust.pem_fingerprint(renewed["ca"])
    assert trust.proxmox_trust_holds("no certificate here", url)["verified"] is False


def test_the_store_is_every_client_s_of_the_run(pve_9, tmp_path, monkeypatch):
    # REQUESTS_CA_BUNDLE is what both uri (as its ca_path) and requests are handed; without it, the standard library's
    # store and certifi's must both verify, since uri uses the one and every community.proxmox module the other.
    pki, url = pve_9
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(pki["ca"])
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    assert trust.proxmox_trust_pick([{"name": "store", "pem": None}], url)["name"] == "store"
    monkeypatch.delenv("REQUESTS_CA_BUNDLE")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    picked = trust.proxmox_trust_pick([{"name": "store", "pem": None}], url)
    assert picked["name"] == "" and picked["refusals"][0]["name"] == "store"


def test_a_block_shaped_as_a_certificate_that_is_none_is_refused_not_raised(pve_9):
    pki, url = pve_9
    fake = "-----BEGIN CERTIFICATE-----\n" + "A" * 64 + "\n-----END CERTIFICATE-----\n"
    picked = trust.proxmox_trust_pick([{"name": "ca", "pem": fake}, {"name": "node", "pem": pki["node"]}], url)
    assert picked["refusals"][0]["name"] == "ca" and picked["refusals"][0]["why"]
    assert trust.pem_fingerprint(fake) == ""


def test_the_modules_http_client_verifies_against_the_file_kept(pve_8, tmp_path):
    # proxmoxer, under every community.proxmox module, hands requests the ca_path as verify=: the file kept is one
    # requests takes as Ansible's uri does.
    import requests

    pki, url = pve_8
    kept = tmp_path / "proxmox-trust.pem"
    kept.write_text(trust.proxmox_trust_pick(_candidates(pki), url)["pem"])
    assert requests.get(f"{url}/version", verify=str(kept), timeout=5).status_code == 401
    renewed = proxmox_pki(tmp_path / "renewed")
    kept.write_text(renewed["node"])
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"{url}/version", verify=str(kept), timeout=5)


def test_every_request_that_carries_the_token_follows_no_redirect():
    # Ansible's uri follows a GET's redirect with its headers, the token's among them: a redirect is an answer, never
    # a place to send the token to. just status refuses redirects too (its _NoRedirect).
    import re

    for path in [*(REPO / "playbooks").rglob("*.yml"), *(REPO / "roles").rglob("*.yml")]:
        if "molecule" in path.parts:
            continue
        for task in re.split(r"(?m)^\s*- name: ", path.read_text()):
            if 'headers: "{{ proxmox_api_headers }}"' in task:
                assert re.search(r"(?m)^\s+follow_redirects: none$", task), (path, task.splitlines()[0])


def test_just_status_says_while_nothing_checks_a_host_s_certificate():
    from claude_vm.status import model, settings

    assert settings.FLEET["settings"]["api_checked"] == "{{ proxmox_api_validate | bool }}"
    for checked, said in ((False, True), (True, False)):
        given = {"api_checked": checked, "host_flag": " --host nuc", "editor_name": "VS Code"}
        fleet = model.status_fleet([], settings=given)
        report = model.status_report({"localhost": {"fleet": fleet}}, "now")
        items = [it for it in report["attention"] if it["vm"] == "proxmox" and "not checked" in it["text"]]
        assert bool(items) is said, report["attention"]
        if said:
            assert items[0]["level"] == "todo" and items[0]["command"] == "just init --host nuc sets checking up"
