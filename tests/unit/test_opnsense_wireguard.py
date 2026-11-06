"""playbooks/library/opnsense_wireguard.py against tests/molecule/fake_opnsense.py, a stand-in OPNsense over TLS.

Each test starts the fake on a loopback port of its own, with a certificate made for it, and runs the module in this
process or, where what a whole run prints matters, through ansible-playbook. Nothing reaches a router.

What these hold:

- A read gives the instance, every peer with the instances it is a peer of, and the instance's handshakes; no
  private or pre-shared key the router hands back reaches the result, a failure, or anything a run prints.
- The router's certificate is held to the pin before any request: a router whose certificate is not the pinned one
  is sent nothing at all, its key least of all, and the message names both fingerprints.
- A refused key is said as one. A router older than the floor is refused once its version is read, before anything
  else is asked of it, in check mode too.
- Changes go deletes first, then updates, then adds, with one reconfigure, and none when nothing changes; check mode
  writes nothing; an update or a delete of a peer not on the instance, or an address another of its peers holds, is
  refused before anything is written; a refusal after a write still reconfigures what was written.
- Both shapes of a refused write - a field's message as a string, or as a list - become a message naming the field;
  OK with its newlines, in JSON or plain text, is a success.
- A listing the router pages is read page by page, one short of its count is not called whole, and a peer a search
  gives without its fields is read alone.
"""

from __future__ import annotations

import base64
import hashlib
import json
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from conftest import fail_main, run_main
from harness import FAKES, REPO, _certificate, _results, _run, _serve, fake_fleet, load_module
from test_status import status_root

wireguard = load_module("playbooks/library/opnsense_wireguard.py")
FAKE = FAKES / "fake_opnsense.py"
API_KEY = "opnsense-test-key-0000"
API_SECRET = "opnsense-test-secret-0000"


def _key(byte: int) -> str:
    """A WireGuard key of 32 bytes all `byte`, as the fake makes its keys: plainly fake, and of no entropy."""
    return base64.b64encode(bytes([byte]) * 32).decode()


# The fake's canned secrets: the instances' private keys, the one it gives an instance a fixture adds, and a
# device's stored private key and pre-shared key. Each is looked for in everything a run returns and prints.
SECRETS = (_key(0x07), _key(0x08), _key(0x09), _key(0x03), _key(0x05))
# The fake's instances and peers.
HOME = "5e1f0000-0000-4000-8000-000000000001"
LAPTOP = "c11e0000-0000-4000-8000-000000000011"
PHONE = "c11e0000-0000-4000-8000-000000000012"
ALPHA = "c11e0000-0000-4000-8000-000000000013"
OLD = "c11e0000-0000-4000-8000-000000000014"
SEARCH_CLIENT = "/api/wireguard/client/search_client"

INSTANCE = {
    "ref": HOME,
    "name": "homeserver",
    "public_key": _key(0x51),
    "port": 51820,
    "addresses": ["198.51.100.1/24"],
    "enabled": True,
}


def _peer(ref: str, name: str, key: str, address: str, instance: str = "homeserver", keepalive: int = 0) -> dict:
    return {
        "ref": ref,
        "name": name,
        "public_key": key,
        "addresses": [address],
        "instances": [instance],
        "enabled": True,
        "keepalive": keepalive,
    }


PEERS = [
    _peer(LAPTOP, "laptop", _key(0x21), "198.51.100.2/32"),
    _peer(PHONE, "phone", _key(0x22), "203.0.113.2/32", instance="other"),
    _peer(ALPHA, "claude-on-proxmox-alpha", _key(0x23), "198.51.100.130/32", keepalive=25),
    _peer(OLD, "claude-on-proxmox-old", _key(0x24), "198.51.100.140/32"),
]
# What runs on the instance: the laptop's handshake, and none yet from either VM. The phone is the other instance's.
HANDSHAKES = {_key(0x21): 1793140625, _key(0x23): 0, _key(0x24): 0}

ADD_BETA = {
    "kind": "add",
    "name": "claude-on-proxmox-beta",
    "public_key": _key(0x61),
    "address": "198.51.100.131/32",
    "keepalive": 25,
}
MOVE_ALPHA = {"kind": "update", "ref": ALPHA, "address": "198.51.100.132/32"}
DROP_OLD = {"kind": "delete", "ref": OLD, "name": "claude-on-proxmox-old"}


@dataclass
class Fake:
    url: str
    pin: str
    state: Path


def _pin(cert: Path) -> str:
    """The certificate's SHA-256 as openssl prints a fingerprint: AB:CD:..."""
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


@pytest.fixture
def router(tmp_path):
    """The fake router on a port of its own, until the test ends."""
    cert, key = _certificate(tmp_path)
    state = tmp_path / "opnsense"
    for base in _serve(FAKE, str(cert), str(key), str(state), API_KEY, API_SECRET):
        yield Fake(base.replace("http://", "https://"), _pin(cert), state)


def _fixture(fake: Fake, path: str, body: dict | None = None) -> dict:
    """One request to the fake's own endpoints, /api/fake/..., which its calls log leaves out."""
    request = urllib.request.Request(
        f"{fake.url}/api/fake/{path}",
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={
            "Authorization": "Basic " + base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode(),
            "Content-Type": "application/json",
        },
    )
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=10, context=unverified) as answer:
        return json.loads(answer.read() or b"{}")


def _calls(fake: Fake) -> list[dict]:
    """Every request the module made of the fake, in order."""
    log = fake.state / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _writes(fake: Fake) -> list[str]:
    return [call["path"] for call in _calls(fake) if call["method"] == "POST"]


def _args(fake: Fake, **extra) -> dict:
    return {
        "url": fake.url,
        "cert_sha256": fake.pin,
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "instance": "homeserver",
        **extra,
    }


def _apply(fake: Fake, changes: list, **extra) -> dict:
    return _args(fake, state="apply", changes=changes, **extra)


def _keys(value) -> set:
    """Every key of every mapping in value, at any depth."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def _no_secret_in(value) -> None:
    text = value if isinstance(value, str) else json.dumps(value)
    assert [secret for secret in (*SECRETS, API_SECRET) if secret in text] == []
    assert "privkey" not in text


# ------------------------------------------------------------------ helpers ---


@pytest.mark.parametrize(
    ("version", "floor", "older"),
    [
        ("26.1.11_10", "26.7.2", True),
        ("26.1.2", "26.7.2", True),
        ("26.7", "26.7.2", True),
        ("26.7.2", "26.7.2", False),
        ("26.7.2_1", "26.7.2", False),
        ("26.10.1", "26.7.2", False),
        ("27.1", "26.7.2", False),
    ],
)
def test_versions_are_compared_as_numbers(version, floor, older):
    assert wireguard.older(wireguard.numbers(version), wireguard.numbers(floor)) is older


def test_option_maps_are_read_as_what_is_selected():
    relation = {HOME: {"value": "homeserver", "selected": 1}, LAPTOP: {"value": "other", "selected": 0}}
    assert wireguard.values(relation) == [HOME]
    assert wireguard.values({"": {"value": "None", "selected": 1}}) == []
    assert wireguard.values("198.51.100.2/32,198.51.100.3/32") == ["198.51.100.2/32", "198.51.100.3/32"]
    assert wireguard.values("") == []


def test_a_secret_is_dropped_at_any_depth():
    nested = {"rows": [{"uuid": "u", "privkey": "a", "%privkey": "b", "inner": {"psk": "c", "keep": [{"PrivKey": 1}]}}]}
    assert wireguard.scrub(nested) == {"rows": [{"uuid": "u", "inner": {"keep": [{}]}}]}


def test_both_shapes_of_a_refusal_are_said_by_field():
    as_string = {"result": "failed", "validations": {"server.privkey": "A value is required."}}
    as_list = {"result": "failed", "validations": {"client.pubkey": ["Public keys should be unique."]}}
    assert wireguard.refusal(as_string) == "server.privkey: A value is required."
    assert wireguard.refusal(as_list) == "client.pubkey: Public keys should be unique."
    assert wireguard.refusal({"result": "failed"}) == "'failed', and nothing more"


@pytest.mark.parametrize("answer", [{"result": "ok"}, {"status": "OK\n\n"}, "OK\n\n"])
def test_ok_with_its_newlines_is_done(answer):
    assert wireguard.done(answer, wireguard.OK)


# --------------------------------------------------------------------- read ---


def test_a_read_gives_the_instance_every_peer_and_its_handshakes_and_never_a_secret(router, patch_module):
    result = run_main(wireguard.main, _args(router))
    assert result["changed"] is False
    assert result["version"] == "26.7.7"
    assert result["instance"] == INSTANCE
    assert result["peers"] == PEERS
    assert result["listed"] is True
    assert result["handshakes"] == HANDSHAKES
    assert result["results"] == [] and result["reconfigured"] is False
    # The module's own dropping, with no Ansible masking behind it: search_server named each instance's private key.
    _no_secret_in(result)
    assert not {"privkey", "%privkey", "psk"} & _keys(result)
    calls = _calls(router)
    assert {call["method"] for call in calls} == {"GET"}
    assert all(call["auth"] and call["accepted"] for call in calls)
    # Never service/status, which says "unknown" while WireGuard runs.
    assert "/api/wireguard/service/status" not in [call["path"] for call in calls]


def test_a_pin_in_lowercase_without_colons_and_a_secret_with_a_newline_are_taken_as_they_are_meant(
    router, patch_module
):
    # A YAML block scalar leaves a newline after the secret; openssl prints the pin in capitals with colons.
    args = _args(router, cert_sha256=router.pin.replace(":", "").lower(), api_secret=API_SECRET + "\n")
    assert run_main(wireguard.main, args)["instance"] == INSTANCE


def test_a_certificate_other_than_the_pinned_one_is_sent_nothing(router, patch_module, tmp_path):
    (tmp_path / "other").mkdir()
    other, _ = _certificate(tmp_path / "other")
    for state in ("read", "apply"):
        failure = fail_main(wireguard.main, _args(router, cert_sha256=_pin(other), state=state, changes=[ADD_BETA]))
        assert failure["msg"] == (
            f"The router's TLS certificate is not the pinned one: its SHA-256 is {router.pin},"
            f" the pin is {_pin(other)}."
        )
        assert failure["changed"] is False
    # TLS was set up and nothing more: no request, so no key, ever reached the router.
    assert _calls(router) == []


@pytest.mark.parametrize(
    ("args", "said"),
    [
        ({"url": "http://127.0.0.1:9"}, "url is not https://<host>[:<port>]."),
        ({"url": "https://127.0.0.1:9/api"}, "url is the router's API base alone"),
        ({"cert_sha256": "AB:CD"}, "cert_sha256 is not a SHA-256 fingerprint"),
        ({"floor": "latest"}, "floor is not a version"),
    ],
)
def test_settings_that_cannot_be_right_are_refused_before_the_router_is_asked(router, patch_module, args, said):
    assert fail_main(wireguard.main, _args(router, **args))["msg"].startswith(said)
    assert _calls(router) == []


def test_a_key_the_router_refuses_is_said_plainly(router, patch_module):
    failure = fail_main(wireguard.main, _args(router, api_secret="wrong-wrong-wrong-0000"))
    assert failure["msg"] == (
        "The router refused the API key and secret (HTTP 401 to GET /api/core/firmware/info): they are not a key"
        " of the router's, or its user is disabled."
    )
    assert "wrong-wrong-wrong-0000" not in json.dumps(failure)
    (call,) = _calls(router)
    assert call["auth"] and not call["accepted"]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize("version", ["26.1.11_10", "26.1.2", "26.7.1"])
def test_a_router_older_than_the_floor_is_asked_nothing_after_its_version(router, patch_module, version, check_mode):
    _fixture(router, "version", {"version": version})
    failure = fail_main(wireguard.main, _apply(router, [ADD_BETA], _ansible_check_mode=check_mode))
    assert failure["msg"].startswith(f"The router runs OPNsense {version}, older than 26.7.2, the oldest")
    assert failure["version"] == version
    assert failure["changed"] is False
    assert [(call["method"], call["path"]) for call in _calls(router)] == [("GET", "/api/core/firmware/info")]


def test_an_instance_that_is_not_there_or_not_alone_is_refused(router, patch_module):
    assert fail_main(wireguard.main, _args(router, instance="nowhere"))["msg"] == (
        "The router has no WireGuard instance named nowhere."
    )
    _fixture(router, "server", {"name": "homeserver", "tunneladdress": "192.0.2.1/24", "port": "51830"})
    failure = fail_main(wireguard.main, _apply(router, [ADD_BETA]))
    assert failure["msg"].startswith("2 WireGuard instances on the router are named homeserver: which one is meant")
    assert _writes(router) == []
    _no_secret_in(failure)


def test_a_listing_the_router_pages_is_read_page_by_page(router, patch_module):
    whole = run_main(wireguard.main, _args(router))
    _fixture(router, "listing", {"cap": 1})
    paged = run_main(wireguard.main, _args(router))
    assert paged["peers"] == whole["peers"] == PEERS
    assert paged["listed"] is True
    asked = [call["query"] for call in _calls(router) if call["path"] == SEARCH_CLIENT]
    assert asked == [{}, {}, *({"current": str(page), "rowCount": "1"} for page in (2, 3, 4))]


def test_a_listing_short_of_its_count_is_not_called_whole(router, patch_module):
    _fixture(router, "listing", {"missing": 1})
    result = run_main(wireguard.main, _args(router))
    assert result["peers"] == PEERS
    assert result["listed"] is False


def test_a_peer_a_search_gives_without_its_fields_is_read_alone(router, patch_module):
    _fixture(router, "listing", {"bare": True})
    result = run_main(wireguard.main, _args(router))
    assert result["peers"] == PEERS
    assert result["listed"] is True
    alone = [call["path"] for call in _calls(router) if "/get_client/" in call["path"]]
    assert alone == [f"/api/wireguard/client/get_client/{ref}" for ref in (LAPTOP, PHONE, ALPHA, OLD)]
    # get_client gives the laptop's stored private key and its pre-shared key.
    _no_secret_in(result)


# -------------------------------------------------------------------- apply ---


def test_changes_are_made_deletes_first_with_one_reconfigure(router, patch_module):
    result = run_main(wireguard.main, _apply(router, [ADD_BETA, MOVE_ALPHA, DROP_OLD]))
    assert result["changed"] is True
    assert result["reconfigured"] is True
    beta = result["results"][0]["ref"]
    assert result["results"] == [
        {"kind": "add", "name": "claude-on-proxmox-beta", "ref": beta, "result": "added"},
        {"kind": "update", "name": "", "ref": ALPHA, "result": "updated"},
        {"kind": "delete", "name": "claude-on-proxmox-old", "ref": OLD, "result": "deleted"},
    ]
    assert _writes(router) == [
        f"/api/wireguard/client/del_client/{OLD}",
        f"/api/wireguard/client/set_client/{ALPHA}",
        "/api/wireguard/client/add_client",
        "/api/wireguard/service/reconfigure",
    ]
    # A peer joins the instance through its servers relation, the instance's uuid.
    (added,) = [call["body"] for call in _calls(router) if call["path"] == "/api/wireguard/client/add_client"]
    assert added == {
        "client": {
            "enabled": "1",
            "name": "claude-on-proxmox-beta",
            "pubkey": _key(0x61),
            "tunneladdress": "198.51.100.131/32",
            "servers": HOME,
            "keepalive": "25",
        }
    }
    state = _fixture(router, "state")
    assert state["reconfigures"] == 1
    assert OLD not in state["clients"]
    assert state["servers"][HOME]["peers"] == [LAPTOP, ALPHA, beta]
    # The update moved the address and kept the rest, the instance among it.
    assert state["clients"][ALPHA]["tunneladdress"] == ["198.51.100.132/32"]
    assert state["clients"][ALPHA]["keepalive"] == "25"
    # What it returns is what the router holds after them, and what runs after the reconfigure.
    peers = {peer["name"]: peer for peer in result["peers"]}
    assert "claude-on-proxmox-old" not in peers
    assert peers["claude-on-proxmox-beta"] == _peer(
        beta, "claude-on-proxmox-beta", _key(0x61), "198.51.100.131/32", keepalive=25
    )
    assert peers["claude-on-proxmox-alpha"]["addresses"] == ["198.51.100.132/32"]
    assert result["handshakes"] == {_key(0x21): 1793140625, _key(0x23): 0, _key(0x61): 0}


def test_a_second_run_of_the_same_changes_writes_nothing_and_reconfigures_nothing(router, patch_module):
    first = run_main(wireguard.main, _apply(router, [ADD_BETA]))
    beta = first["results"][0]["ref"]
    writes = len(_writes(router))
    again = run_main(wireguard.main, _apply(router, [ADD_BETA, {"kind": "update", "ref": ALPHA, "keepalive": 25}]))
    assert again["changed"] is False
    assert again["reconfigured"] is False
    assert [(change["ref"], change["result"]) for change in again["results"]] == [
        (beta, "unchanged"),
        (ALPHA, "unchanged"),
    ]
    nothing = run_main(wireguard.main, _apply(router, []))
    assert nothing["changed"] is False
    assert nothing["results"] == []
    assert len(_writes(router)) == writes
    assert _fixture(router, "state")["reconfigures"] == 1


def test_a_peer_a_failed_reconfigure_left_saved_is_applied_when_asked_with_no_change(router, patch_module):
    # The reconfigure after the add fails: beta is saved, and WireGuard runs without it. A later run that asks for
    # it reconfigures with nothing to write; check mode asks the router for nothing.
    _fixture(router, "reconfigure", {"answer": "failed"})
    fail_main(wireguard.main, _apply(router, [ADD_BETA]))
    _fixture(router, "reconfigure", {"answer": "result"})
    saved = run_main(wireguard.main, _args(router))
    assert ADD_BETA["public_key"] not in saved["handshakes"]
    writes = len(_writes(router))
    checked = run_main(wireguard.main, _apply(router, [], reconfigure=True, _ansible_check_mode=True))
    assert checked["changed"] is True and checked["reconfigured"] is False and len(_writes(router)) == writes
    again = run_main(wireguard.main, _apply(router, [], reconfigure=True))
    assert again["changed"] is True and again["reconfigured"] is True
    assert _writes(router)[writes:] == ["/api/wireguard/service/reconfigure"]
    assert ADD_BETA["public_key"] in again["handshakes"]


def test_an_address_a_delete_frees_is_free_for_an_add_of_the_same_run(router, patch_module):
    reuse = {**ADD_BETA, "address": "198.51.100.140/32"}
    result = run_main(wireguard.main, _apply(router, [reuse, DROP_OLD]))
    assert [change["result"] for change in result["results"]] == ["added", "deleted"]
    assert _writes(router) == [
        f"/api/wireguard/client/del_client/{OLD}",
        "/api/wireguard/client/add_client",
        "/api/wireguard/service/reconfigure",
    ]


def test_check_mode_writes_nothing(router, patch_module):
    before = _fixture(router, "state")
    result = run_main(wireguard.main, _apply(router, [ADD_BETA, MOVE_ALPHA, DROP_OLD], _ansible_check_mode=True))
    assert result["changed"] is True
    assert result["reconfigured"] is False
    assert [(change["ref"], change["result"]) for change in result["results"]] == [
        ("", "added"),
        (ALPHA, "updated"),
        (OLD, "deleted"),
    ]
    assert result["peers"] == PEERS
    assert {call["method"] for call in _calls(router)} == {"GET"}
    assert _fixture(router, "state") == before


@pytest.mark.parametrize("kind", ["update", "delete"])
@pytest.mark.parametrize(
    "ref",
    [
        # A peer of the other instance, and one the router does not have.
        PHONE,
        "c11e0000-0000-4000-8000-0000000000ff",
    ],
)
def test_an_update_or_a_delete_of_a_peer_not_on_the_instance_is_refused(router, patch_module, kind, ref):
    change = {"kind": kind, "ref": ref, **({"keepalive": 30} if kind == "update" else {})}
    # The add before it would be made, were anything made before every change was checked.
    failure = fail_main(wireguard.main, _apply(router, [ADD_BETA, change]))
    assert failure["msg"] == (
        f"Change 2 ({kind} {ref}): {ref} is not a peer of the instance homeserver as read just now:"
        " nothing was changed."
    )
    assert failure["changed"] is False
    assert _writes(router) == []


def test_a_delete_of_a_peer_named_otherwise_now_is_refused(router, patch_module):
    failure = fail_main(wireguard.main, _apply(router, [{"kind": "delete", "ref": OLD, "name": "claude-on-proxmox-x"}]))
    assert failure["msg"].endswith(f"the peer {OLD} is named claude-on-proxmox-old now: nothing was changed.")
    assert _writes(router) == []


@pytest.mark.parametrize(
    ("address", "said"),
    [
        # The user's own device's address: WireGuard would route it to the VM instead.
        ("198.51.100.2/32", "198.51.100.2/32 is held by the peer laptop of the instance: nothing was changed."),
        ("192.0.2.10/32", "address 192.0.2.10/32 is not inside the instance's tunnel networks, or is its own."),
        ("198.51.100.1/32", "address 198.51.100.1/32 is not inside the instance's tunnel networks, or is its own."),
        ("198.51.100.0/25", "address 198.51.100.0/25 is not an IPv4 address with /32."),
    ],
)
def test_an_address_the_instance_cannot_give_a_vm_is_refused(router, patch_module, address, said):
    failure = fail_main(wireguard.main, _apply(router, [{**ADD_BETA, "address": address}]))
    assert failure["msg"] == f"Change 1 (add claude-on-proxmox-beta): {said}"
    assert _writes(router) == []


def test_a_public_key_that_is_not_one_is_refused_and_never_said(router, patch_module):
    # Thirty-one bytes: one short of a key, and close enough to one that a message must not carry it.
    short = base64.b64encode(bytes([0x0A]) * 31).decode()
    failure = fail_main(wireguard.main, _apply(router, [{**ADD_BETA, "public_key": short}]))
    assert failure["msg"] == (
        "Change 1 (add claude-on-proxmox-beta): public_key is not a WireGuard public key, 32 bytes in base64."
    )
    assert _writes(router) == []


@pytest.mark.parametrize(
    ("change", "said"),
    [
        # The laptop's key: the router answers with a list of messages for the field.
        ({**ADD_BETA, "public_key": _key(0x21)}, "client.pubkey: Public keys should be unique."),
        # A name its model's mask refuses: a string.
        (
            {**ADD_BETA, "name": "beta with spaces"},
            "client.name: Should be a string between 1 and 64 characters, of letters, digits, '.', '_' or '-'.",
        ),
    ],
)
def test_a_write_the_router_refuses_is_said_with_its_reasons(router, patch_module, change, said):
    failure = fail_main(wireguard.main, _apply(router, [change]))
    assert failure["msg"] == f"Change 1 (add {change['name']}) was refused by the router: {said}"
    assert failure["changed"] is False
    assert failure["reconfigured"] is False
    assert [made["result"] for made in failure["results"]] == ["refused"]
    assert _writes(router) == ["/api/wireguard/client/add_client"]
    _no_secret_in(failure)


def test_a_refusal_after_a_write_still_reconfigures_what_was_written(router, patch_module):
    refused = {**ADD_BETA, "public_key": _key(0x21)}
    late = {**ADD_BETA, "name": "claude-on-proxmox-gamma", "public_key": _key(0x62), "address": "198.51.100.133/32"}
    failure = fail_main(wireguard.main, _apply(router, [refused, DROP_OLD, late]))
    assert failure["msg"].endswith("was refused by the router: client.pubkey: Public keys should be unique.")
    assert failure["changed"] is True
    assert failure["reconfigured"] is True
    assert [change["result"] for change in failure["results"]] == ["refused", "deleted", "not made"]
    assert _writes(router) == [
        f"/api/wireguard/client/del_client/{OLD}",
        "/api/wireguard/client/add_client",
        "/api/wireguard/service/reconfigure",
    ]
    assert _fixture(router, "state")["reconfigures"] == 1


@pytest.mark.parametrize("answer", ["status", "text"])
def test_ok_with_its_newlines_from_the_router_is_a_success(router, patch_module, answer):
    _fixture(router, "reconfigure", {"answer": answer})
    result = run_main(wireguard.main, _apply(router, [ADD_BETA]))
    assert result["reconfigured"] is True
    assert result["changed"] is True


def test_a_reconfigure_that_fails_is_said(router, patch_module):
    _fixture(router, "reconfigure", {"answer": "failed"})
    failure = fail_main(wireguard.main, _apply(router, [ADD_BETA]))
    assert failure["msg"] == "The changes were saved, but WireGuard's reconfigure answered: 'failed', and nothing more"
    assert failure["changed"] is True
    assert failure["reconfigured"] is False


# ------------------------------------------------------- through a playbook ---


def test_a_playbook_run_prints_no_secret_and_its_check_mode_writes_nothing(router, tmp_path):
    refused = {**ADD_BETA, "public_key": _key(0x21)}
    tasks = [
        {"name": "Read", "opnsense_wireguard": _args(router)},
        {"name": "Plan", "opnsense_wireguard": _apply(router, [ADD_BETA]), "check_mode": True},
        {"name": "Refused", "opnsense_wireguard": _apply(router, [refused]), "ignore_errors": True},
    ]
    playbook = tmp_path / "wireguard.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "tasks": tasks}]))
    report = _run(tmp_path, playbook, {}, timeout=120)
    assert report["rc"] == 0, report["stderr"]
    said = {name: result for name, _, result in _results(report)}
    assert said["Read"]["instance"] == INSTANCE
    assert said["Read"]["peers"] == PEERS
    assert said["Read"]["handshakes"] == HANDSHAKES
    assert said["Plan"]["changed"] is True
    assert [change["result"] for change in said["Plan"]["results"]] == ["added"]
    assert said["Refused"]["failed"] is True
    assert said["Refused"]["msg"].endswith("client.pubkey: Public keys should be unique.")
    # Everything the run printed, its failure and the module's arguments included.
    _no_secret_in(json.dumps(report) + report["stderr"])
    assert not {"privkey", "%privkey", "psk"} & _keys(report)
    # The check run wrote nothing; the refused add is the run's one write, and nothing was reconfigured.
    assert _writes(router) == ["/api/wireguard/client/add_client"]


def test_configure_writes_the_vm_s_peer_on_the_router_and_a_second_run_writes_nothing(router, tmp_path):
    # configure.yml's plan play as it is, against the fake Proxmox and the fake router: the plan updates alpha's peer
    # - the project's, by every mark - to the VM's key and the address it is given, and the router applies it once.
    # Nothing but the module's own tests had run it with an API hub: its write was skipped on every run.
    for base in fake_fleet(tmp_path):
        settings = {
            "vpn": "wireguard",
            "wireguard_hub": "opnsense",
            "wireguard_hub_instance": "homeserver",
            "wireguard_hub_cert_sha256": router.pin,
            "wireguard_hub_api_key": API_KEY,
            "wireguard_hub_api_secret": API_SECRET,
            "wireguard_vm_prefix": "198.51.100.128/25",
            "wireguard_hub_endpoint": "192.0.2.1:51820",
        }
        root, _ = status_root(tmp_path, base, settings)
        (root / "hub.yml").write_text(
            yaml.safe_dump(
                {"wireguard_hub": {"hosts": {"router": {"ansible_host": router.url.removeprefix("https://")}}}}
            )
        )
        plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
        (plan,) = [play for play in plays if play.get("name") == "Plan each VM's place on the WireGuard network"]
        playbook = root / "plan.yml"
        playbook.write_text(yaml.safe_dump([plan]).replace("tasks/vpn_", f"{REPO / 'playbooks' / 'tasks'}/vpn_"))
        alpha = {
            "ansible_connection": "local",
            "claude_vm_listed_id": 9010,
            "claude_vm_marks": [],
            "wireguard_public_key": _key(0x61),
        }
        args = ("-i", str(root / "hub.yml"), "-e", json.dumps({"vm_name": ["alpha"]}))
        first = _run(root, playbook, {"alpha": alpha}, *args, timeout=180)
        assert first["rc"] == 0, first["stderr"]
        state = _fixture(router, "state")
        assert state["clients"][ALPHA]["pubkey"] == _key(0x61)
        assert state["clients"][ALPHA]["tunneladdress"] == ["198.51.100.129/32"]
        assert state["reconfigures"] == 1
        writes = len(_writes(router))
        second = _run(root, playbook, {"alpha": alpha}, *args, timeout=180)
        assert second["rc"] == 0, second["stderr"]
        assert len(_writes(router)) == writes and _fixture(router, "state")["reconfigures"] == 1
