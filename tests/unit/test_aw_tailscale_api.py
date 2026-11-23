"""Tailscale's API (lib/claude_vm/vendors/tailscale.py; docs/architecture/engine.md §16.7).

Every call goes to the fake Tailscale (tests/molecule/fake_tailscale_api.py) over TLS, serving a node's certificate that
tests/unit/pki.py made, whose CA stands in for one of certifi's public roots: certifi.where points at a file of it, as
tests/unit/test_aw_vendor_trust.py does, so the client verifies the fake as it would verify api.tailscale.com. An OAuth
client is exchanged for an access token, held as a Secret from the moment it is read; a device is read and deleted by
its node's ID, with that token or with an API access token; a 404 is a device not there, which a deletion calls gone
already; a 401 or a 403 is Denied. A node ID that is not one Tailscale gives - a slash, a newline, a dot - never reaches
the fake (its /fake/asked lists every request made of the API), and the pattern it is held to is the tailscale role's.
A certificate that does not verify sends nothing. What the fake does not say - a redirect, a body that is no answer,
Tailscale's words echoing a secret - a stand-in transport answers. The rules with a test of their own: no error says the
client secret or a token, even where Tailscale's words echo it, and no frame a Malformed is raised through holds the
token an exchange answered; a base address written with the API's own path reads devices as the host's does; a node ID
is said cut at 200 and sent whole; and every error survives a copy and a pickle.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import pickle
import re
import ssl
from pathlib import Path

import certifi
import pytest
import yaml
from harness import FAKES, REPO, _start
from pki import proxmox_pki
from test_aw_transport import _said

from claude_vm.secret import MARK, Secret
from claude_vm.vendors import tailscale, trust
from claude_vm.vendors.tailscale import (
    NODE_ID,
    Denied,
    Device,
    Malformed,
    NotANodeId,
    Refused,
    Tailscale,
    TailscaleError,
)
from claude_vm.vendors.transport import Answer, NotMade, Transport, Unknown, Untrusted

# The module's two certificate authorities: the fake's, and one that signed nothing the fake shows. Cheap enough to make
# again on each worker (tests/unit/conftest.py).
EACH_WORKER = {"pki", "other_pki"}
FAKE = FAKES / "fake_tailscale_api.py"
ROLE_VARS = REPO / "roles" / "tailscale" / "vars" / "main.yml"
# The tailnet's devices the fake starts with, by node ID.
DEVICES = {"nALPHA1CNTRL": "alpha", "nBETA2CNTRL": "beta", "nGAMMA3CNTRL": "gamma"}
# The fake's credentials (its docstring): the one OAuth client it takes, the token it answers for it, an API access
# token it takes, and one it refuses every device (403).
CLIENT_ID = "k-test-client"
CLIENT_SECRET = "tskey-client-good-0000"
OAUTH_TOKEN = "tskey-api-oauth-0000"
API_TOKEN = "tskey-api-good-0000"
DENIED_TOKEN = "tskey-api-denied-0000"
# A secret no output may show: low in entropy, so that gitleaks takes it for no key.
CANARY = "unit-test-tailscale-canary-value"
# Node IDs Tailscale never gives, each of which could take a token elsewhere or reach the fake as another ID: a slash
# and a path that climbs, a newline ($ lets one through, \Z does not), a dot, a query, a fragment, an escape, a space,
# letters and digits outside ASCII (\w and \d take them), nothing, and what is no str.
NOT_IDS = [
    "n/../../tailnet/-/keys",
    "nALPHA1CNTRL/",
    "nALPHA1CNTRL\n",
    "nALPHA1CNTRL.",
    "..",
    ".",
    "nALPHA1CNTRL?fields=all",
    "nALPHA1CNTRL#x",
    "nALPHA1CNTRL%2F",
    "n ALPHA1CNTRL",
    "ｎALPHA1CNTRL",
    "١٢٣",
    "",
    42,
    None,
]


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return proxmox_pki(tmp_path_factory.mktemp("pki"), key_usage=True)


@pytest.fixture(scope="module")
def other_pki(tmp_path_factory):
    """A CA of another subject than the fake's: OpenSSL finds no issuer of the fake's certificate in a file of it."""
    return proxmox_pki(tmp_path_factory.mktemp("other-pki"), key_usage=True, name="Another Test CA")


def _file(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text)
    return path


@dataclasses.dataclass
class Fake:
    """The fake Tailscale at base, over TLS with the certificate pki made; certifi's list stood in for by its CA."""

    base: str
    pki: dict

    def _read(self, what: str) -> list:
        """What the fake says of itself at /fake/<what>, read through a context of its CA alone."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=self.pki["ca"])
        with Transport(self.base, context=context, user_agent="test") as reader:
            return json.loads(reader.request("GET", f"fake/{what}").body)

    def asked(self) -> list[str]:
        """The path of every request the client made of the API, as sent."""
        return self._read("asked")

    def deleted(self) -> list[str]:
        return self._read("deleted")

    def exchanged(self) -> list[str]:
        return self._read("exchanged")

    def client(self, **kwargs) -> Tailscale:
        return Tailscale(self.base, **kwargs)


def _certifi(monkeypatch, directory: Path, pem: str) -> None:
    """certifi's list stood in for by a file of pem: a root of certifi's, as api.tailscale.com's is."""
    stood_in = _file(directory, "certifi.pem", pem)
    monkeypatch.setattr(certifi, "where", lambda: str(stood_in))


@pytest.fixture
def fake(tmp_path, pki, monkeypatch):
    _certifi(monkeypatch, tmp_path, pki["ca"])
    server, port = _start(FAKE, "--tls", str(pki["cert"]), str(pki["key"]), *(f"{n}={m}" for n, m in DEVICES.items()))
    try:
        yield Fake(f"https://127.0.0.1:{port}", pki)
    finally:
        server.terminate()
        server.wait(timeout=10)


# ------------------------------------------------------------------------------------------- the exchange ----


def test_an_oauth_client_is_exchanged_for_an_access_token_held_as_a_secret(fake) -> None:
    with fake.client() as client:
        token = client.access_token(CLIENT_ID, Secret(CLIENT_SECRET))
        # The ID may come from the vault as a Secret too.
        again = client.access_token(Secret("k-held-client"), Secret(CLIENT_SECRET))
        shown = repr(client) + repr(token) + str(token) + f"{token}"
    assert isinstance(token, Secret) and token.reveal() == OAUTH_TOKEN and again.reveal() == OAUTH_TOKEN
    assert OAUTH_TOKEN not in shown and CLIENT_SECRET not in shown and MARK in shown
    assert fake.exchanged() == [CLIENT_ID, "k-held-client"]
    assert fake.asked() == ["/api/v2/oauth/token"] * 2


def test_the_exchange_sends_the_client_as_a_form_its_secret_a_secret_until_the_transport() -> None:
    """The client secret, and an ID held as one, are handed to the transport as Secrets, which it alone reveals; the
    form is OAuth's client credentials grant."""
    client = Tailscale("https://127.0.0.1:9")
    given = _Answers(Answer(200, (), json.dumps({"access_token": OAUTH_TOKEN, "token_type": "Bearer"}).encode()))
    client._transport = given
    secret = Secret(CLIENT_SECRET)
    client.access_token(CLIENT_ID, secret)
    (method, path), sent = given.asked[0]
    assert (method, path) == ("POST", "oauth/token")
    assert sent["form"] == {"client_id": CLIENT_ID, "client_secret": secret, "grant_type": "client_credentials"}
    assert sent["form"]["client_secret"] is secret and sent.get("query") is None and sent["deadline"] > 0


def test_a_client_tailscale_does_not_take_is_denied_by_its_status(fake) -> None:
    with fake.client() as client, pytest.raises(Denied) as denied:
        client.access_token(CLIENT_ID, Secret(CANARY))
    assert denied.value.status == 401
    assert str(denied.value) == "Tailscale did not take the OAuth client (401) for POST /api/v2/oauth/token"
    assert fake.exchanged() == [CLIENT_ID]


def test_the_secrets_are_secrets_and_the_client_s_id_one_line(fake) -> None:
    with fake.client() as client:
        with pytest.raises(TypeError, match="an OAuth client's secret is a Secret"):
            client.access_token(CLIENT_ID, CLIENT_SECRET)  # type: ignore[arg-type]
        for given in (f"{CLIENT_ID}\n", "", "a b", 7):
            with pytest.raises(ValueError, match="an OAuth client's ID is one line of visible ASCII"):
                client.access_token(given, Secret(CLIENT_SECRET))  # type: ignore[arg-type]
        for call in (client.device, client.delete_device):
            with pytest.raises(TypeError, match="an access token is a Secret"):
                call(API_TOKEN, "nALPHA1CNTRL")  # type: ignore[arg-type]
    assert fake.asked() == []


# -------------------------------------------------------------------------------------------- the devices ----


@pytest.mark.parametrize("by", ["the exchanged token", "an API access token"])
def test_a_device_is_read_by_its_node_id(fake, by) -> None:
    with fake.client() as client:
        token = client.access_token(CLIENT_ID, Secret(CLIENT_SECRET)) if by.startswith("the") else Secret(API_TOKEN)
        found = client.device(token, "nALPHA1CNTRL")
    assert found == Device("nALPHA1CNTRL", "alpha.tail1234.ts.net", "ubuntu")
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.name = "beta"  # type: ignore[misc]
    assert fake.asked()[-1] == "/api/v2/device/nALPHA1CNTRL"


def test_a_device_that_is_not_there_reads_as_none(fake) -> None:
    with fake.client() as client:
        assert client.device(Secret(API_TOKEN), "nNOSUCH0CNTRL") is None
    assert fake.asked() == ["/api/v2/device/nNOSUCH0CNTRL"]


def test_a_device_is_deleted_once_and_then_is_gone_already(fake) -> None:
    with fake.client() as client:
        token = client.access_token(CLIENT_ID, Secret(CLIENT_SECRET))
        assert client.delete_device(token, "nBETA2CNTRL") is True
        assert fake.deleted() == ["nBETA2CNTRL"]
        # A 404: deleted already, or in a tailnet the token does not reach. Never an error.
        assert client.delete_device(token, "nBETA2CNTRL") is False
        assert client.device(token, "nBETA2CNTRL") is None
        assert client.delete_device(token, "nNOSUCH0CNTRL") is False
        # The other devices stay.
        assert client.device(token, "nGAMMA3CNTRL") == Device("nGAMMA3CNTRL", "gamma.tail1234.ts.net", "ubuntu")
    assert fake.deleted() == ["nBETA2CNTRL"]
    assert fake.asked()[1:] == [
        "/api/v2/device/nBETA2CNTRL",
        "/api/v2/device/nBETA2CNTRL",
        "/api/v2/device/nBETA2CNTRL",
        "/api/v2/device/nNOSUCH0CNTRL",
        "/api/v2/device/nGAMMA3CNTRL",
    ]


def test_a_token_that_may_not_reach_the_device_is_the_typed_refusal(fake) -> None:
    """A 403, which an access token an OAuth client was exchanged for meets on a device without its tags: Denied, by its
    status alone, and nothing deleted; why is the workflow's to say, which knows which credential the token is. A token
    Tailscale does not take is Denied too, as a 401."""
    with fake.client() as client:
        for call, asked in ((client.device, "GET"), (client.delete_device, "DELETE")):
            with pytest.raises(Denied) as denied:
                call(Secret(DENIED_TOKEN), "nGAMMA3CNTRL")
            assert denied.value.status == 403
            assert (
                str(denied.value) == f"Tailscale refused {asked} /api/v2/device/nGAMMA3CNTRL to the access token (403)"
            )
            with pytest.raises(Denied) as denied:
                call(Secret(CANARY), "nGAMMA3CNTRL")
            assert denied.value.status == 401
            assert str(denied.value) == (
                f"Tailscale did not take the access token (401) for {asked} /api/v2/device/nGAMMA3CNTRL"
            )
    assert fake.deleted() == []


def test_an_id_that_is_not_one_tailscale_gives_never_reaches_the_fake(fake) -> None:
    with fake.client() as client:
        for given in NOT_IDS:
            for call in (client.device, client.delete_device):
                with pytest.raises(NotANodeId) as refused:
                    call(Secret(API_TOKEN), given)  # type: ignore[arg-type]
                said = str(refused.value)
                assert said.endswith(
                    "is not one Tailscale gives (letters and digits), so Tailscale's API was not asked"
                )
                # One line of ASCII, what was given shown as it is: its newline escaped, never let through.
                assert said.isascii() and said.isprintable(), said
                assert isinstance(refused.value, ValueError) and isinstance(refused.value, TailscaleError)
    assert fake.asked() == [] and fake.deleted() == []
    assert 'the node ID "nALPHA1CNTRL\\n" is not one' in str(NotANodeId("nALPHA1CNTRL\n"))
    assert str(NotANodeId(42)).startswith("the node ID (a value of type int) is not one")


def test_a_long_node_id_is_said_cut_at_200_characters_and_sent_whole() -> None:
    # The machine chose its length: what an error says of it is cut, as today's release cuts it, and the path holds it
    # whole, since a cut one would be another device's.
    long = "n" * 300
    client = _stand_in(Answer(500, (), b"", "Internal Server Error"), Answer(403, (), b"", "Forbidden"))
    with pytest.raises(Refused) as refused:
        client.device(Secret(API_TOKEN), long)
    with pytest.raises(Denied) as denied:
        client.delete_device(Secret(API_TOKEN), long)
    assert [path for (_, path), _ in client._transport.asked] == [f"device/{long}"] * 2  # type: ignore[attr-defined]
    assert str(refused.value) == f"Tailscale answered GET /api/v2/device/{'n' * 200}… with 500: Internal Server Error"
    assert str(denied.value) == f"Tailscale refused DELETE /api/v2/device/{'n' * 200}… to the access token (403)"
    assert str(NotANodeId("n/" + "n" * 300)).startswith('the node ID "n/' + "n" * 198 + '" is not one')
    assert tailscale.SHOWN == 200


@pytest.mark.parametrize(
    "error",
    [
        Denied(403, "GET /api/v2/device/nALPHA1CNTRL", "the access token"),
        Denied(401, "POST /api/v2/oauth/token", "the OAuth client"),
        Refused(500, "internal error", "GET /api/v2/device/nALPHA1CNTRL"),
        Refused(302, "", "POST /api/v2/oauth/token"),
        NotANodeId("n/x"),
        NotANodeId(42),
    ],
)
def test_each_error_survives_a_copy_and_a_pickle_and_says_its_sentence_once(error) -> None:
    for made in (copy.copy(error), copy.deepcopy(error), pickle.loads(pickle.dumps(error))):
        assert type(made) is type(error) and str(made) == str(error)
        assert getattr(made, "status", None) == getattr(error, "status", None)


def test_a_base_address_written_with_the_api_s_own_path_reads_devices_as_the_host_s(fake) -> None:
    # As Tailscale's documentation writes it: with the path put on twice every device would read as gone already.
    for base in (f"{fake.base}/api/v2", f"{fake.base}/api/v2/"):
        with Tailscale(base) as client:
            assert repr(client) == f"Tailscale({fake.base!r})"
            assert client.device(Secret(API_TOKEN), "nALPHA1CNTRL") == Device(
                "nALPHA1CNTRL", "alpha.tail1234.ts.net", "ubuntu"
            )
    assert fake.asked() == ["/api/v2/device/nALPHA1CNTRL"] * 2


def test_the_node_id_pattern_is_the_tailscale_role_s() -> None:
    """Today's release holds the ID with Ansible's match test, re.match of '^[A-Za-z0-9]+\\Z'; the client with
    fullmatch of its pattern. The same text, and the same answer for every ID here."""
    role = yaml.safe_load(ROLE_VARS.read_text())["tailscale_node_id_pattern"]
    assert role == f"^{NODE_ID.pattern}\\Z"
    for given in [*DEVICES, "n1", "1234567890", *(one for one in NOT_IDS if isinstance(one, str))]:
        assert bool(re.match(role, given)) == bool(NODE_ID.fullmatch(given)), given


# ------------------------------------------------------------------------------------------- the connection ----


def test_a_certificate_that_does_not_verify_sends_nothing_and_tls_ca_file_adds_its_root(
    fake, other_pki, tmp_path, monkeypatch
) -> None:
    """certifi's list holds another CA alone: the fake's certificate is Untrusted, its sentence naming tls_ca_file, and
    nothing reaches it. The setting naming the fake's CA adds it to certifi's, and the call goes."""
    _certifi(monkeypatch, tmp_path, other_pki["ca"])
    with fake.client() as client, pytest.raises(Untrusted) as refused:
        client.access_token(CLIENT_ID, Secret(CLIENT_SECRET))
    assert trust.SETTING in str(refused.value)
    assert fake.asked() == [] and fake.exchanged() == []
    with fake.client(tls_ca_file=_file(tmp_path, "company.pem", fake.pki["ca"])) as client:
        assert client.device(Secret(API_TOKEN), "nALPHA1CNTRL") is not None
    with pytest.raises(trust.Unusable, match=trust.SETTING):
        fake.client(tls_ca_file=tmp_path / "no-such.pem")


def test_the_address_is_https_alone() -> None:
    for base in ("http://127.0.0.1:9", "ftp://127.0.0.1", "https://user@127.0.0.1"):
        with pytest.raises(ValueError):
            Tailscale(base)
    assert repr(Tailscale("https://api.tailscale.com/")) == "Tailscale('https://api.tailscale.com')"


# ---------------------------------------------------------------------------- what the fake does not say ----


class _Answers:
    """A transport that answers each request with the next of answers, or raises it, and keeps what each was asked."""

    def __init__(self, *given: Answer | BaseException) -> None:
        self.given = list(given)
        self.asked: list = []

    def request(self, method: str, path: str, **kwargs) -> Answer:
        self.asked.append(((method, path), kwargs))
        one = self.given.pop(0)
        if isinstance(one, BaseException):
            raise one
        return one

    def close(self) -> None:
        pass


def _stand_in(*given: Answer | BaseException) -> Tailscale:
    client = Tailscale("https://127.0.0.1:9")
    client._transport = _Answers(*given)  # type: ignore[assignment]
    return client


def test_a_redirect_is_an_answer_never_followed() -> None:
    elsewhere = Answer(302, (("Location", "https://elsewhere.test/api/v2/oauth/token"),), b"", "Found")
    with pytest.raises(Refused) as refused:
        _stand_in(elsewhere).access_token(CLIENT_ID, Secret(CLIENT_SECRET))
    assert refused.value.status == 302
    assert str(refused.value) == (
        "Tailscale answered POST /api/v2/oauth/token with a redirect (302), which is never followed"
    )
    with pytest.raises(Refused, match="a redirect"):
        _stand_in(elsewhere).delete_device(Secret(API_TOKEN), "nALPHA1CNTRL")


def test_any_other_answer_is_refused_with_tailscale_s_words_made_plain() -> None:
    said = Answer(500, (), json.dumps({"message": "internal\x1b[31m error\nnow"}).encode(), "Internal Server Error")
    with pytest.raises(Refused) as refused:
        _stand_in(said).device(Secret(API_TOKEN), "nALPHA1CNTRL")
    assert str(refused.value) == "Tailscale answered GET /api/v2/device/nALPHA1CNTRL with 500: internal error now"
    oauth = Answer(400, (), json.dumps({"error": "invalid_request"}).encode(), "Bad Request")
    with pytest.raises(Refused, match=r"with 400: invalid_request\Z"):
        _stand_in(oauth).access_token(CLIENT_ID, Secret(CLIENT_SECRET))
    bare = Answer(429, (), b"", "Too Many Requests")
    with pytest.raises(Refused, match=r"with 429: Too Many Requests\Z"):
        _stand_in(bare).delete_device(Secret(API_TOKEN), "nALPHA1CNTRL")


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"token_type": "Bearer"}).encode(),
        json.dumps({"access_token": f"{CANARY}\nX-Injected: 1"}).encode(),
        json.dumps({"access_token": CANARY, "token_type": "mac"}).encode(),
        json.dumps({"access_token": ""}).encode(),
        json.dumps({"access_token": 7}).encode(),
    ],
)
def test_an_exchange_s_200_that_holds_no_bearer_token_is_malformed_and_never_quoted(body, caplog, capsys) -> None:
    with pytest.raises(Malformed) as malformed:
        _stand_in(Answer(200, (), body, "OK")).access_token(CLIENT_ID, Secret(CLIENT_SECRET))
    assert str(malformed.value) == (
        "Tailscale answered POST /api/v2/oauth/token with something that is not an access token"
    )
    assert not [text for text in _said(malformed.value, caplog, capsys, frames=False) if CANARY in text]
    # Nor any frame of the client's that it was raised through: the answer and the token were read in a frame of their
    # own, which ended first (this test's own frame holds the body it gave).
    held, tb = [], malformed.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == tailscale.__file__:
            held += [value if isinstance(value, str) else repr(value) for value in tb.tb_frame.f_locals.values()]
        tb = tb.tb_next
    assert held and not [text for text in held if CANARY in text], held


def test_a_device_s_200_that_is_no_device_is_malformed() -> None:
    for body in (b"[]", b"", b'"nALPHA1CNTRL"'):
        with pytest.raises(Malformed, match="with something that is not a device"):
            _stand_in(Answer(200, (), body, "OK")).device(Secret(API_TOKEN), "nALPHA1CNTRL")
    # A field that is no string, or text that is not plain, is read as Tailscale's words are.
    odd = json.dumps({"name": 7, "hostname": "web\x1b[2J\n2"}).encode()
    assert _stand_in(Answer(200, (), odd, "OK")).device(Secret(API_TOKEN), "nWEB2") == Device("nWEB2", "", "web 2")


def test_a_deletion_that_left_with_no_answer_is_the_transport_s_unknown_sent_once() -> None:
    client = _stand_in(Unknown("no answer came"), Answer(200, (), b"{}", "OK"))
    with pytest.raises(Unknown):
        client.delete_device(Secret(API_TOKEN), "nALPHA1CNTRL")
    assert len(client._transport.asked) == 1  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------------------------- the secrets ----

ECHOES = {
    "whole": CANARY,
    "as the header": f"Bearer {CANARY}",
    "as the form": f"client_id={CLIENT_ID}&client_secret={CANARY}",
    "cut by a control character": f"{CANARY[:7]}\x07{CANARY[7:]} refused",
    "where the words are cut": "x" * 180 + CANARY,
}
CALLS = {
    "the exchange": lambda client: client.access_token(CLIENT_ID, Secret(CANARY)),
    "the exchange, its ID a Secret": lambda client: client.access_token(Secret(CANARY), Secret(CANARY)),
    "a read": lambda client: client.device(Secret(CANARY), "nALPHA1CNTRL"),
    "a deletion": lambda client: client.delete_device(Secret(CANARY), "nALPHA1CNTRL"),
}


@pytest.mark.parametrize("where", ["message", "error_description", "error", "status line"])
@pytest.mark.parametrize("echo", ECHOES.values(), ids=ECHOES.keys())
@pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
def test_no_error_says_a_secret_even_where_tailscale_s_words_echo_it(call, echo, where, caplog, capsys) -> None:
    """Tailscale, or a proxy the computer's certificates trust, may put what it was sent in its words: the client
    strikes the secrets the call sent from them before it says any, as they came and once made plain, and before it cuts
    them. The frames of the traceback hold the answer as it came, the peer's own words, and are not looked at: what is
    said is."""
    caplog.set_level(logging.DEBUG)
    body = b"" if where == "status line" else json.dumps({where: echo}).encode()
    client = _stand_in()
    for status in (400, 500):
        client._transport = _Answers(Answer(status, (), body, echo if where == "status line" else "Refused"))
        with pytest.raises(Refused) as refused:
            call(client)
        said = [*_said(refused.value, caplog, capsys, frames=False), refused.value.reason]
        # No part of it either: no ten characters of it in a row, which a secret struck only once its words were cut
        # would leave.
        parts = {CANARY[at : at + 10] for at in range(len(CANARY) - 9)}
        assert not [text for text in said if any(part in text for part in parts)], said
        if echo == ECHOES["whole"]:
            assert MARK in str(refused.value)
        assert refused.value.__cause__ is None and refused.value.__context__ is None


def test_no_error_of_the_client_against_the_fake_says_a_secret(
    fake, other_pki, tmp_path, monkeypatch, caplog, capsys
) -> None:
    caplog.set_level(logging.DEBUG)
    raised = []

    def caught(client: Tailscale, call) -> None:
        try:
            call(client)
        except Exception as error:  # noqa: BLE001 - each is what the test reads
            raised.append(error)

    with fake.client() as client:
        for call in CALLS.values():
            caught(client, call)
        caught(client, lambda client: client.device(Secret(CANARY), "n/../../tailnet/-/keys"))
        caught(client, lambda client: client.device(Secret(f"{CANARY}\r\nX-Injected: 1"), "nALPHA1CNTRL"))
    _certifi(monkeypatch, tmp_path, other_pki["ca"])
    with fake.client() as client:
        caught(client, CALLS["the exchange"])
    assert [type(error) for error in raised] == [Denied, Denied, Denied, Denied, NotANodeId, NotMade, Untrusted]
    for error in raised:
        said = "\n".join(_said(error, caplog, capsys))
        assert CANARY not in said, type(error)
        assert error.__cause__ is None and error.__context__ is None


def test_the_client_holds_no_secret_and_returns_none_it_was_given(fake) -> None:
    token = Secret(API_TOKEN)
    with fake.client() as client:
        got = [client.device(token, "nALPHA1CNTRL"), client.delete_device(token, "nALPHA1CNTRL")]
        shown = repr(client) + repr(vars(client))
    assert API_TOKEN not in repr(got) and API_TOKEN not in shown
    assert not any(isinstance(one, Secret) for one in got)
