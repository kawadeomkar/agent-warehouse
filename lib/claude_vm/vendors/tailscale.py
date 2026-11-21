"""Tailscale's API: an OAuth client exchanged for an access token, and a device read and deleted by its node's ID, as
today's release deletes one (docs/architecture/engine.md §16.7).

Three calls, on the one transport, verified against the computer's certificates (vendors/trust.py: certifi's list, with
the file the setting tls_ca_file names added), through the proxy its caller gives: POST /api/v2/oauth/token, the
client's ID and secret as form fields; GET and DELETE /api/v2/device/<node ID>, carrying an access token as a Bearer
token - the one an OAuth client was exchanged for, or an API access token held as it is. The base address is its
caller's (today's tailscale_api_url), so a test points it at tests/molecule/fake_tailscale_api.py.

The node's ID is what a machine reported, so the machine chooses it, and an access token may delete any device of the
tailnet: it goes into the path only once it is held whole, with fullmatch, to NODE_ID, the pattern of today's
tailscale_node_id_pattern, letters and digits alone, so that no / or .. takes the token to another endpoint. One that
is not is NotANodeId, and nothing is sent. A 404 is a device that is not there - deleted already, or in a tailnet the
token does not reach: device() answers None, and delete_device() False. A 401 or a 403 is Denied, by its status alone;
a 403 is the refusal of a token that may not reach that device, as an OAuth client's reaches only the devices with its
tags. Any other answer but the call's own is Refused, with Tailscale's words, the secrets the call sent struck from
them before they are made plain and cut (Secret.struck), so that a peer that echoes them has none of them said. A 3xx is
an answer, never followed. A 200 that is not the answer asked for is Malformed, and never quoted: an exchange's body
holds a token. The transport's own errors pass as they are: a DELETE that left with no answer is its Unknown, never sent
again, which the workflow settles by reading the device.

Which device may be deleted (the name rules of today's release), which credential is used where none is held, and what
is said to a person are the release workflow's, never this module's. The client secret and every access token are a
Secret, revealed by the transport alone; the access token an exchange answers is one from the moment it is read. No
method returns a secret the caller gave, and no error says one. Blocking, and safe to call from several threads, as the
transport is.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from typing import Any

from ..secret import Secret
from ..text import one_line
from . import trust
from .transport import Answer, Transport

__all__ = ["Denied", "Device", "Malformed", "NotANodeId", "Refused", "Tailscale", "TailscaleError"]

# Where every path of the API is, under the base address.
API = "/api/v2"
# A node's stable ID as Tailscale gives it, Self.ID in tailscale status --json, which the API calls nodeId and takes as
# a device's ID: letters and digits. Held whole, with fullmatch, and equal to the tailscale role's
# tailscale_node_id_pattern, '^[A-Za-z0-9]+\Z' (tests/unit/test_aw_tailscale_api.py).
NODE_ID = re.compile(r"[A-Za-z0-9]+")
# What the client calls itself in a request's User-Agent: the Proxmox client's own word (pve/pin.py).
USER_AGENT = "agent-warehouse"
# A call's deadline, its connection included, in seconds: today's uri's 30 s for each of its waits, twice.
DEADLINE = 60.0
# An answer's body at most, in bytes: a device's JSON is a few kilobytes.
MOST = 1024 * 1024
# How much of Tailscale's words a refusal says, once made one line of plain text; and of a node ID refused, before it
# is shown.
WORDS = 200
SHOWN = 60
# An access token as an exchange answers one: visible ASCII, which a header's value carries as it is.
TOKEN = re.compile(r"[!-~]{1,4096}")
# A client's ID given as a str: visible ASCII, one line.
CLIENT_ID = re.compile(r"[!-~]{1,256}")
# Why a token may be refused a device (403): today's words for it (roles/tailscale/vars/main.yml).
TAGGED = "an access token an OAuth client was exchanged for reaches only the devices with that client's tags"
# The keys of a refusal's JSON that carry Tailscale's words, in the order they are read: the API's own, and OAuth's.
SAYS = ("message", "error_description", "error")


class TailscaleError(Exception):
    """A call to Tailscale's API refused, in this module's words, which never say a secret."""


class NotANodeId(TailscaleError, ValueError):
    """The node ID given is not one Tailscale gives, so Tailscale's API was not asked. The sentence shows what the
    machine reported as it is, cut: as JSON, ASCII alone, so that a newline or an escape is seen and never let through
    (today's said it plain, where a trailing newline, the very fault, did not show)."""

    def __init__(self, value: object) -> None:
        given = json.dumps(value[:SHOWN]) if isinstance(value, str) else f"(a value of type {type(value).__name__})"
        super().__init__(
            f"the node ID {given} is not one Tailscale gives (letters and digits), so Tailscale's API was not asked"
        )


class Denied(TailscaleError):
    """Tailscale refused the credential: a 401, which it did not take, or a 403, which may not do what was asked. By its
    status alone, never its words. For a device, a 403 says why it may be: an access token an OAuth client was
    exchanged for reaches only the devices with that client's tags (TAGGED)."""

    def __init__(self, status: int, asked: str, credential: str, why: str = "") -> None:
        self.status = status
        if status == 401:
            said = f"Tailscale did not take {credential} (401) for {asked}"
        else:
            said = f"Tailscale refused {asked} to {credential} (403)" + (f": {why}" if why else "")
        super().__init__(said)


class Refused(TailscaleError):
    """Any other answer but the call's own: its status, and Tailscale's words, the secrets the call sent struck from
    them, made one line of plain text. A 3xx is one, never followed: a secret goes to the address it was given alone."""

    def __init__(self, status: int, reason: str, asked: str) -> None:
        self.status = status
        self.reason = reason
        if 300 <= status < 400:
            said = f"Tailscale answered {asked} with a redirect ({status}), which is never followed"
        else:
            said = f"Tailscale answered {asked} with {status}" + (f": {reason}" if reason else "")
        super().__init__(said)


class Malformed(TailscaleError):
    """Tailscale answered 200 with something that is not the answer asked for, which is never quoted."""


@dataclasses.dataclass(frozen=True)
class Device:
    """A device of the tailnet, as Tailscale's API gives it: the node ID it was read by, and its MagicDNS name
    (<machine name>.<tailnet>) and its host name, each made one line of plain text, "" where Tailscale gives none.
    Whether it is the one a release may delete is the workflow's to decide from them."""

    node_id: str
    name: str
    hostname: str


def _node(node_id: object) -> str:
    """node_id, held whole to NODE_ID; NotANodeId, with nothing sent, where it is not."""
    if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
        raise NotANodeId(node_id)
    return node_id


def _secret(value: object, what: str) -> Secret:
    if not isinstance(value, Secret):
        raise TypeError(f"{what} is a Secret")
    return value


def _json(body: bytes) -> Any:
    """The answer's JSON; None where it is none, with nothing raised that holds it."""
    try:
        return json.loads(body, parse_constant=lambda name: None)
    except (RecursionError, UnicodeDecodeError, ValueError):
        return None


def _text(value: Any, most: int) -> str:
    return one_line(value, most) if isinstance(value, str) else ""


def _words(answer: Answer, secrets: tuple[Secret, ...]) -> str:
    """Tailscale's words of a refusal - its JSON's message, an OAuth error's description or name, else its status
    line's - as they may be said: one line of plain text, each secret the call sent struck from them as they came and
    again once made plain, before they are cut, so that no part of one a peer echoed is said."""
    found = _json(answer.body)
    words = answer.reason
    if isinstance(found, dict):
        words = next((found[key] for key in SAYS if isinstance(found.get(key), str) and found[key].strip()), words)
    for secret in secrets:
        words = secret.struck(words)
    words = one_line(words, None)
    for secret in secrets:
        words = secret.struck(words)
    return one_line(words, WORDS)


class Tailscale:
    """The client of Tailscale's API at base_url (https://api.tailscale.com, today's tailscale_api_url), its
    connections verified against the computer's certificates and the file tls_ca_file names, where it names one,
    through proxy, an http:// URL, where there is one (proxy.choose works out today's for the API's address). Raises
    trust.Unusable where those certificates cannot be used."""

    def __init__(
        self, base_url: str, tls_ca_file: str | os.PathLike[str] | None = None, proxy: str | None = None
    ) -> None:
        if not isinstance(base_url, str):
            raise TypeError("the API's address is a str")
        self._base = base_url.rstrip("/")
        self._transport = Transport(
            f"{self._base}{API}", context=trust.context(tls_ca_file), user_agent=USER_AGENT, most=MOST, proxy=proxy
        )

    def __enter__(self) -> Tailscale:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close every connection kept: for when no request is in flight."""
        self._transport.close()

    def __repr__(self) -> str:
        return f"Tailscale({self._base!r})"

    def _refusal(
        self, answer: Answer, asked: str, credential: str, secrets: tuple[Secret, ...], why: str = ""
    ) -> TailscaleError:
        """The refusal an answer that is not the call's own is: Denied by a 401's or a 403's status alone, else
        Refused with Tailscale's words, secrets struck."""
        if answer.status in (401, 403):
            return Denied(answer.status, asked, credential, why)
        return Refused(answer.status, _words(answer, secrets), asked)

    def access_token(self, client_id: str | Secret, client_secret: Secret) -> Secret:
        """POST /api/v2/oauth/token: the OAuth client, its ID (a str or a Secret) and its secret, exchanged for an
        access token, a Secret, which lasts an hour. Raises Denied where Tailscale did not take the client, Refused for
        any other answer but a 200, Malformed for a 200 that holds no Bearer token."""
        _secret(client_secret, "an OAuth client's secret")
        if not isinstance(client_id, Secret) and not (isinstance(client_id, str) and CLIENT_ID.fullmatch(client_id)):
            raise ValueError("an OAuth client's ID is one line of visible ASCII, or a Secret")
        asked = f"POST {API}/oauth/token"
        form = {"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"}
        answer = self._transport.request("POST", "oauth/token", form=form, deadline=DEADLINE)
        if answer.status != 200:
            sent = (client_secret, client_id) if isinstance(client_id, Secret) else (client_secret,)
            raise self._refusal(answer, asked, "the OAuth client", sent)
        found = _json(answer.body)
        token = found.get("access_token") if isinstance(found, dict) else None
        kind = found.get("token_type", "Bearer") if isinstance(found, dict) else None
        if not (
            isinstance(token, str) and TOKEN.fullmatch(token) and isinstance(kind, str) and kind.lower() == "bearer"
        ):
            raise Malformed(f"Tailscale answered {asked} with something that is not an access token")
        return Secret(token)

    def device(self, token: Secret, node_id: str) -> Device | None:
        """GET /api/v2/device/<node_id>: the device, or None where Tailscale has none under that ID (404) - gone, or
        in a tailnet the token does not reach. Raises NotANodeId, with nothing sent, for an ID that is not one Tailscale
        gives; Denied for a 401 or a 403; Refused for any other answer; Malformed for a 200 that is no device."""
        path = f"device/{_node(node_id)}"
        asked = f"GET {API}/{path}"
        answer = self._transport.request(
            "GET", path, headers={"Authorization": ("Bearer ", _secret(token, "an access token"))}, deadline=DEADLINE
        )
        if answer.status == 404:
            return None
        if answer.status != 200:
            raise self._refusal(answer, asked, "the access token", (token,), TAGGED)
        found = _json(answer.body)
        if not isinstance(found, dict):
            raise Malformed(f"Tailscale answered {asked} with something that is not a device")
        return Device(node_id, _text(found.get("name"), 255), _text(found.get("hostname"), 255))

    def delete_device(self, token: Secret, node_id: str) -> bool:
        """DELETE /api/v2/device/<node_id>, sent once: True where it deleted the device (200, 204), False where
        Tailscale has none under that ID (404), deleted already or in a tailnet the token does not reach. Raises
        NotANodeId, with nothing sent, for an ID that is not one Tailscale gives; Denied for a 401 or a 403, a token
        that may not delete that device; Refused for any other answer. The transport's Unknown, a deletion that left
        with no answer, passes as it is: the workflow reads the device to know."""
        path = f"device/{_node(node_id)}"
        asked = f"DELETE {API}/{path}"
        answer = self._transport.request(
            "DELETE", path, headers={"Authorization": ("Bearer ", _secret(token, "an access token"))}, deadline=DEADLINE
        )
        if answer.status in (200, 204):
            return True
        if answer.status == 404:
            return False
        raise self._refusal(answer, asked, "the access token", (token,), TAGGED)
