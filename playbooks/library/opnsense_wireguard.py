#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Read, and change, the peers of one WireGuard instance on an OPNsense router, through its API."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: opnsense_wireguard
short_description: Read and change the peers of one WireGuard instance on an OPNsense router
version_added: "1.1.0"
description:
  - Runs on the controller, delegated to localhost. Talks to the router's REST API over HTTPS, with the router's own
    certificate pinned by O(cert_sha256): TLS is set up without checking the chain or the name, and the
    certificate's SHA-256 is compared with the pin before any request, and so the key, is sent on the connection.
  - Refuses a router whose OPNsense is older than O(floor), before anything else is asked of it, in check mode too.
  - Reads the instance named O(instance) - which must be the only one of that name - every peer ("client") of the
    router, with the instances each is a peer of, and each peer's latest handshake on that instance.
  - With O(state=apply), adds, updates and deletes peers of that instance as O(changes) says, then reconfigures
    WireGuard once, only when something was written. An update or a delete is refused unless its O(changes[].ref)
    is a peer of the instance as read just before, and so is an address held by another of its peers or outside its
    tunnel networks: every change is checked before the first is sent. Deletes go first, then updates, then adds.
    A change the router refuses ends the run; what was written before it is still reconfigured.
  - Never changes the instance, another instance, WireGuard's on switch, or anything else of the router's.
  - Every private key the router hands back - the instance's, which it lists in plain text, and a peer's, as 26.7
    stores one - and every pre-shared key is dropped from each answer as it arrives, before anything is kept, and
    again from the result. No message carries a key or the API secret.
options:
  url:
    description: The router's API base, C(https://<host>[:<port>]), without a path.
    type: str
    required: true
  cert_sha256:
    description:
      - The SHA-256 of the router's TLS certificate, in DER, as hex; colons and either case are fine, as
        C(openssl x509 -noout -fingerprint -sha256) prints it.
    type: str
    required: true
  api_key:
    description: The API key, for HTTP Basic authentication.
    type: str
    required: true
    no_log: true
  api_secret:
    description: The API key's secret.
    type: str
    required: true
    no_log: true
  instance:
    description: The name of the WireGuard instance whose peers are read and changed.
    type: str
    required: true
  floor:
    description:
      - The oldest OPNsense this module talks to. 26.7.2 is the first with the fix for FreeBSD-SA-26:52, under which
        WireGuard accepted forged packets.
    type: str
    default: "26.7.2"
  state:
    description: C(read) changes nothing; C(apply) makes O(changes).
    type: str
    choices: [read, apply]
    default: read
  changes:
    description: What to change on the instance, under O(state=apply); ignored under C(read).
    type: list
    elements: dict
    default: []
    suboptions:
      kind:
        description: C(add) a peer of the instance, C(update) one, or C(delete) one.
        type: str
        required: true
        choices: [add, update, delete]
      name:
        description:
          - The peer's name. Required to add. To update, its new name. To delete, the name it must still have.
        type: str
      ref:
        description: The peer's uuid on the router, to update or delete it; never given to add.
        type: str
      public_key:
        description: The peer's WireGuard public key. Required to add.
        type: str
      address:
        description: The peer's tunnel address, an IPv4 /32 inside the instance's tunnel networks. Required to add.
        type: str
      keepalive:
        description: Seconds between keepalives; 0 for none. Left as it is when not given.
        type: int
  reconfigure:
    description:
      - Under O(state=apply), reconfigure WireGuard on the router even when no change is made: a peer saved but not
        running, after a reconfigure that failed or never came, is applied. Nothing in check mode.
    type: bool
    default: false
  timeout:
    description: Seconds to wait for the router on each request.
    type: int
    default: 30
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Read the instance and its peers
  opnsense_wireguard:
    url: https://192.0.2.1
    cert_sha256: "AB:CD:...:EF"
    api_key: "{{ vault_opnsense_api_key }}"
    api_secret: "{{ vault_opnsense_api_secret }}"
    instance: homeserver
  delegate_to: localhost
  register: wireguard

- name: Make the VMs' peers what they should be
  opnsense_wireguard:
    url: https://192.0.2.1
    cert_sha256: "AB:CD:...:EF"
    api_key: "{{ vault_opnsense_api_key }}"
    api_secret: "{{ vault_opnsense_api_secret }}"
    instance: homeserver
    state: apply
    changes:
      - kind: add
        name: claude-on-proxmox-alpha
        public_key: "{{ alpha_public_key }}"
        address: 198.51.100.130/32
        keepalive: 25
      - kind: delete
        ref: 0f3c2a1e-5b6d-4c7e-8f90-123456789abc
        name: claude-on-proxmox-gone
  delegate_to: localhost
"""

RETURN = r"""
version:
  description: The OPNsense version the router runs, as it says it.
  returned: when the router answered
  type: str
  sample: 26.7.7
instance:
  description:
    - The instance - C(ref) its uuid, C(name), C(public_key), C(port) (an int, or none), C(addresses) (its tunnel
      addresses with their prefix, such as C(198.51.100.1/24)) and C(enabled) - never its private key.
  returned: when it was found
  type: dict
peers:
  description:
    - Every peer of the router, after the changes (in check mode, or when a change failed, as read before them), in
      the order the router lists them - C(ref) its uuid, C(name),
      C(public_key), C(addresses) (a list), C(instances) (the names of the instances it is a peer of), C(enabled)
      and C(keepalive) (0 for none) - never a private or pre-shared key.
  returned: when the instance was found
  type: list
  elements: dict
listed:
  description: Whether every peer the router counts was read, every page of its listing and each peer read alone.
  returned: always
  type: bool
handshakes:
  description:
    - The latest handshake of each peer of the instance as it runs, by public key, in seconds since the epoch; 0 for
      none yet. A peer added since WireGuard was last reconfigured is not there.
  returned: when the instance was found
  type: dict
results:
  description:
    - One for each of O(changes), in their order - C(kind), C(name), C(ref) (an added peer's new uuid; empty in check
      mode) and C(result), C(added), C(updated), C(deleted) or C(unchanged), as made or, in check mode, as it would be.
      When a change fails, its result is C(refused), and a change after it that was never sent is C(not made).
  returned: always
  type: list
  elements: dict
reconfigured:
  description: Whether WireGuard was reconfigured, which happens once whenever something was written.
  returned: always
  type: bool
"""

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import ssl
from urllib.parse import quote, urlsplit

from ansible.module_utils.basic import AnsibleModule

# Fields that hold a secret in what OPNsense answers: the instance's private key, which search_server and get_server
# give in plain text, a peer's, which 26.7 stores for its peer generator, and a pre-shared key.
SECRETS = frozenset({"privkey", "psk"})
# A WireGuard key, 32 bytes in base64: never said in a message, whoever's it is.
KEY_SHAPE = re.compile(r"[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=")
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# The most of one answer read, and the most pages of one listing asked for.
MOST = 16 << 20
PAGES = 200
SEARCH_SERVER = "/api/wireguard/server/search_server"
SEARCH_CLIENT = "/api/wireguard/client/search_client"
# The version the router runs is product.product_version of firmware/info, as WIREGUARD-PLAN §1.3 read it live.
# firmware/status is the last update check's, and its version is what is on offer: never what runs.
FIRMWARE = "/api/core/firmware/info"
# A write's answer, and what it says when done: {"result": "saved"}, {"result": "deleted"}, and reconfigure's
# {"result": "ok"} - or {"status": "OK\n\n"}, or that as plain text.
SAVED, DELETED, OK = {"saved"}, {"deleted"}, {"ok"}
# Deletes first, so an address or a key one frees is free for an add of the same run; then updates, then adds.
ORDER = {"delete": 0, "update": 1, "add": 2}


class Refused(Exception):
    """What ends the run: said as it is, after the secrets are taken out of it."""


def scrub(value):
    """``value`` without any field that holds a secret, at any depth."""
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items() if str(key).lstrip("%").lower() not in SECRETS}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def plain(text, most=300):
    """Text of the router's, or of an error, fit for a message: one line's worth, no control characters, no key."""
    text = KEY_SHAPE.sub("<key>", CONTROL.sub(" ", str(text))).strip()
    return text if len(text) <= most else text[:most] + "..."


def colons(digest):
    """A SHA-256 in hex as openssl prints a fingerprint: AB:CD:..."""
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def pin_of(text):
    """The pin as 64 lowercase hex digits, or Refused."""
    digest = re.sub(r"[\s:]", "", text or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refused("cert_sha256 is not a SHA-256 fingerprint: 64 hex digits, colons allowed.")
    return digest


def numbers(version):
    """A version's leading numbers as a tuple - "26.1.11_10" is (26, 1, 11) - or None."""
    found = re.match(r"\s*(\d+(?:\.\d+)*)", str(version or ""))
    return tuple(int(part) for part in found.group(1).split(".")) if found else None


def older(version, floor):
    """Whether the version is older than the floor, both as numbers(), the shorter padded with zeros."""
    width = max(len(version), len(floor))
    return version + (0,) * (width - len(version)) < floor + (0,) * (width - len(floor))


def on(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def whole(value):
    """An int of OPNsense's, given as "51820", "" or 25; None when it is none."""
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() else None


def values(field):
    """What a field holds, as a list: a comma-joined string, a list, or an option map - {key: {value, selected}},
    as get_server and get_client give a relation or a list of addresses - of which only the selected keys."""
    if isinstance(field, dict):
        return [
            str(key) for key, item in field.items() if key != "" and isinstance(item, dict) and on(item.get("selected"))
        ]
    if isinstance(field, list):
        return [str(item).strip() for item in field if str(item).strip()]
    if field is None:
        return []
    return [part.strip() for part in str(field).split(",") if part.strip()]


class PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to the router, its certificate held to the pin as soon as TLS is up and before anything is sent on it -
    also when http.client opens the connection again by itself."""

    def __init__(self, host, port, pin, timeout):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # The router's certificate is its own, self-signed: the pin is the whole check, never the chain or the name.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        super().__init__(host, port, timeout=timeout, context=context)
        self.pin = pin

    def connect(self):
        super().connect()
        seen = hashlib.sha256(self.sock.getpeercert(binary_form=True) or b"").hexdigest()
        if not hmac.compare_digest(seen, self.pin):
            self.close()
            raise Refused(
                f"The router's TLS certificate is not the pinned one: its SHA-256 is {colons(seen)},"
                f" the pin is {colons(self.pin)}."
            )


class Router:
    """The router's API: one pinned connection per request, the key on it only once the pin has held."""

    def __init__(self, url, pin, key, secret, timeout):
        parts = urlsplit(url or "")
        try:
            port = parts.port or 443
        except ValueError:
            port = None
        if parts.scheme != "https" or not parts.hostname or port is None:
            raise Refused("url is not https://<host>[:<port>].")
        if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise Refused("url is the router's API base alone, https://<host>[:<port>]: no path, query or user.")
        self.host, self.port, self.pin, self.timeout = parts.hostname, port, pin, timeout
        self.token = base64.b64encode(f"{key}:{secret}".encode()).decode()

    def where(self):
        return f"{self.host}:{self.port}"

    def call(self, method, path, body=None):
        """One request; the answer's JSON, every secret field dropped, or its text when it is not JSON."""
        connection = PinnedConnection(self.host, self.port, self.pin, self.timeout)
        try:
            # TLS up and the certificate held to the pin before the request, which carries the key, is sent.
            connection.connect()
            headers = {
                "Authorization": f"Basic {self.token}",
                "Accept": "application/json",
                "User-Agent": "agent-warehouse",
            }
            data = None
            if method == "POST":
                data = json.dumps({} if body is None else body).encode()
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=data, headers=headers)
            answer = connection.getresponse()
            status, raw = answer.status, answer.read(MOST + 1)
        except ssl.SSLError as error:
            raise Refused(f"TLS with the router at {self.where()} failed: {plain(error.reason or error)}") from None
        except (OSError, http.client.HTTPException) as error:
            raise Refused(f"The router at {self.where()} did not answer {method} {path}: {plain(error)}") from None
        finally:
            connection.close()
        if len(raw) > MOST:
            raise Refused(f"The router's answer to {method} {path} is larger than {MOST >> 20} MiB.")
        text = raw.decode("utf-8", "replace")
        try:
            parsed = scrub(json.loads(text))
        except ValueError:
            parsed = None
        if status == 401:
            raise Refused(
                f"The router refused the API key and secret (HTTP 401 to {method} {path}): they are not a key"
                " of the router's, or its user is disabled."
            )
        if status == 403:
            # The version is the firmware page's to say: a user with the WireGuard privilege alone may not read it.
            needs = "System: Firmware, to say which OPNsense it runs" if path == FIRMWARE else "it"
            raise Refused(
                f"The router does not let this API key's user {method} {path} (HTTP 403): the user lacks the"
                f" privilege for {needs}."
            )
        if status != 200:
            raise Refused(f"The router answered {method} {path} with HTTP {status}{said(parsed, text)}.")
        return text if parsed is None else parsed


def said(parsed, text):
    """What an answer says, as ": <it>" for a message, or ""."""
    if isinstance(parsed, dict):
        for field in ("message", "errorMessage", "status", "result"):
            if parsed.get(field) not in (None, ""):
                return f": {plain(parsed[field])}"
        return f": {plain(json.dumps(parsed))}"
    return f": {plain(text)}" if text.strip() else ""


def done(answer, meaning):
    """Whether a write's answer says it was done: its result or status, or its plain text, is one of meaning."""
    if isinstance(answer, dict):
        saying = answer.get("result", answer.get("status", ""))
    else:
        saying = answer
    return isinstance(saying, str) and saying.strip().lower() in meaning


def words(message):
    """One field's validation message: a string, as {"server.privkey": "A value is required."}, or a list of them,
    as {"client.pubkey": ["Public keys should be unique."]}."""
    if isinstance(message, list):
        return " ".join(words(item) for item in message)
    if isinstance(message, dict):
        return " ".join(words(item) for item in message.values())
    return str(message)


def refusal(answer):
    """What the router said refusing a write: each field's message, or its words, or that it said nothing more."""
    if not isinstance(answer, dict):
        return plain(answer) or "it said nothing more"
    found = answer.get("validations")
    if isinstance(found, dict):
        parts = [f"{field}: {words(message)}" for field, message in found.items()]
    elif isinstance(found, list):
        parts = [words(message) for message in found]
    elif isinstance(found, str):
        parts = [found]
    else:
        parts = []
    if not parts and answer.get("message"):
        parts = [str(answer["message"])]
    if not parts:
        saying = answer.get("result", answer.get("status"))
        parts = [f"{saying!r}, and nothing more" if saying else "it said nothing more"]
    return plain("; ".join(parts), most=600)


def search(router, path):
    """Every row of a search endpoint, and whether the router gave all it counts. The first page is asked for
    without paging, which OPNsense answers with every row; a router that pages anyway is asked for each next page
    at the size it gave, until the count is reached or a page brings nothing new."""
    rows, seen, page, query = [], set(), 1, ""
    while True:
        answer = router.call("GET", f"{path}{query}")
        batch = answer.get("rows") if isinstance(answer, dict) else None
        if not isinstance(batch, list):
            raise Refused(f"The router's answer to GET {path} is not a listing{said(answer, str(answer))}.")
        total = whole(answer.get("total"))
        total = len(batch) if total is None else total
        new = [row for row in batch if isinstance(row, dict) and row.get("uuid") not in seen]
        for row in new:
            seen.add(row.get("uuid"))
            rows.append(row)
        if page == 1:
            size = max(len(batch), 1)
        if len(rows) >= total or not new or page >= PAGES:
            return rows, len(rows) == total
        page += 1
        query = f"?current={page}&rowCount={size}"


def version_of(router):
    answer = router.call("GET", FIRMWARE)
    product = answer.get("product") if isinstance(answer, dict) else None
    version = product.get("product_version") if isinstance(product, dict) else None
    if not isinstance(version, str) or numbers(version) is None:
        raise Refused(f"The router did not say which OPNsense it runs: GET {FIRMWARE} has no product.product_version.")
    return plain(version, most=40)


def read(router, name):
    """The instance named name (with its number and its peers' uuids, for this module alone), every peer, whether
    every one was read, and the names of the instances by uuid."""
    servers, servers_listed = search(router, SEARCH_SERVER)
    named = [row for row in servers if row.get("name") == name]
    if not named:
        among = "" if servers_listed else ", among the instances it listed"
        raise Refused(f"The router has no WireGuard instance named {plain(name, 80)}{among}.")
    if len(named) > 1:
        raise Refused(
            f"{len(named)} WireGuard instances on the router are named {plain(name, 80)}: which one is meant cannot"
            " be told, so nothing is read or changed."
        )
    row = named[0]
    if any(field not in row for field in ("tunneladdress", "pubkey", "peers")):
        detail = router.call("GET", f"/api/wireguard/server/get_server/{quote(str(row.get('uuid')), safe='')}")
        server = detail.get("server") if isinstance(detail, dict) else None
        row = {**row, **server} if isinstance(server, dict) else row
    instance = {
        "ref": str(row.get("uuid") or ""),
        "name": name,
        "public_key": str(row.get("pubkey") or ""),
        "port": whole(row.get("port")),
        "addresses": values(row.get("tunneladdress")),
        "enabled": on(row.get("enabled")),
        "number": str(row.get("instance") or ""),
    }
    names = {str(server.get("uuid")): str(server.get("name") or "") for server in servers}
    # A peer belongs to an instance through the instance's peers, which a client's servers mirrors (a volatile
    # relation on 26.7's Client.xml): either says it.
    attached = {}
    for server in servers:
        for ref in values(server.get("peers")):
            attached.setdefault(ref, []).append(str(server.get("uuid")))
    clients, listed = search(router, SEARCH_CLIENT)
    peers = []
    for client in clients:
        ref = str(client.get("uuid") or "")
        if any(field not in client for field in ("servers", "tunneladdress", "pubkey")):
            detail = router.call("GET", f"/api/wireguard/client/get_client/{quote(ref, safe='')}")
            found = detail.get("client") if isinstance(detail, dict) else None
            if isinstance(found, dict):
                client = {**client, **found}
            else:
                listed = False
        refs = values(client.get("servers"))
        refs += [server for server in attached.get(ref, []) if server not in refs]
        peers.append(
            {
                "ref": ref,
                "name": str(client.get("name") or ""),
                "public_key": str(client.get("pubkey") or ""),
                "addresses": values(client.get("tunneladdress")),
                "instances": [names.get(server) or server for server in refs],
                "enabled": on(client.get("enabled")),
                "keepalive": whole(client.get("keepalive")) or 0,
                "servers": refs,
            }
        )
    return instance, peers, listed and servers_listed


def handshakes_of(router, instance):
    """{public key: latest handshake} of the instance's peers as WireGuard runs them, from service/show - never
    service/status, which says "unknown" while it runs."""
    answer = router.call("GET", "/api/wireguard/service/show")
    rows = answer.get("rows") if isinstance(answer, dict) else None
    interface = f"wg{instance['number']}" if instance["number"].isdigit() else None
    shakes = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("type") != "peer":
            continue
        # The instance's name, as service/show gives it; else its interface, wg<its number>.
        if "ifname" in row:
            ours = row["ifname"] == instance["name"]
        else:
            ours = interface is not None and row.get("if") == interface
        key = str(row.get("public-key") or "")
        if ours and key:
            shakes[key] = whole(row.get("latest-handshake")) or 0
    return shakes


def public(instance, peers):
    """The instance and the peers as the module returns them: without what it keeps for itself."""
    shown = {key: value for key, value in instance.items() if key != "number"}
    return shown, [{key: value for key, value in peer.items() if key != "servers"} for peer in peers]


def key_of(text, which):
    text = str(text or "").strip()
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError:
        raw = b""
    if len(text) != 44 or len(raw) != 32:
        raise Refused(f"{which}: public_key is not a WireGuard public key, 32 bytes in base64.")
    return text


def address_of(text, which, networks):
    try:
        network = ipaddress.ip_network(str(text or "").strip(), strict=True)
    except ValueError:
        network = None
    if network is None or network.version != 4 or network.prefixlen != 32:
        raise Refused(f"{which}: address {plain(text, 60)} is not an IPv4 address with /32.")
    inside = [net for net in networks if network.subnet_of(net.network) and network.network_address != net.ip]
    if not inside:
        raise Refused(f"{which}: address {network} is not inside the instance's tunnel networks, or is its own.")
    return str(network)


def plan(changes, instance, peers):
    """Each change as its request - (path, body, result, which) - or None where it would change nothing. Refused for
    any change that cannot be made, before a single one is sent."""
    networks = []
    for address in instance["addresses"]:
        try:
            networks.append(ipaddress.ip_interface(address))
        except ValueError:
            continue
    networks = [net for net in networks if net.version == 4]
    by_ref = {peer["ref"]: peer for peer in peers}
    ours = {peer["ref"] for peer in peers if instance["ref"] in peer["servers"]}
    named = [change.get("ref") for change in changes if change.get("ref")]
    if len(named) != len(set(named)):
        raise Refused("Two changes name the same ref: nothing was changed.")
    going = {change["ref"] for change in changes if change["kind"] == "delete"}
    # What each address of the instance's peers is held by, once the deletes are made.
    held = {address: peer["ref"] for peer in peers if peer["ref"] in ours - going for address in peer["addresses"]}
    requests = []
    for number, change in enumerate(changes, 1):
        kind, ref = change["kind"], change.get("ref") or ""
        which = f"Change {number} ({kind} {plain(change.get('name') or ref, 80)})"
        if kind == "add":
            if ref:
                raise Refused(f"{which}: an add names no ref, the router gives it one.")
            if not change.get("name") or not change.get("public_key") or not change.get("address"):
                raise Refused(f"{which}: an add needs name, public_key and address.")
            wanted = {
                "name": change["name"],
                "public_key": key_of(change["public_key"], which),
                "addresses": [address_of(change["address"], which, networks)],
            }
            if change.get("keepalive") is not None:
                wanted["keepalive"] = max(change["keepalive"], 0)
            same = [
                peer
                for peer in peers
                if peer["ref"] in ours - going and all(peer[field] == value for field, value in wanted.items())
            ]
            if same:
                requests.append(None)
                change["ref"] = same[0]["ref"]
                continue
            body = {
                "enabled": "1",
                "name": wanted["name"],
                "pubkey": wanted["public_key"],
                "tunneladdress": wanted["addresses"][0],
                # The relation that makes it a peer of the instance: Client.xml's servers, a list of uuids.
                "servers": instance["ref"],
            }
            if "keepalive" in wanted:
                body["keepalive"] = str(wanted["keepalive"] or "")
            requests.append(("/api/wireguard/client/add_client", {"client": body}, "added", which))
        else:
            if not UUID.match(ref):
                raise Refused(f"{which}: ref is not a peer's uuid.")
            if ref not in ours:
                raise Refused(
                    f"{which}: {ref} is not a peer of the instance {plain(instance['name'], 80)} as read just now:"
                    " nothing was changed."
                )
            peer = by_ref[ref]
            if kind == "delete":
                if change.get("name") and change["name"] != peer["name"]:
                    raise Refused(
                        f"{which}: the peer {ref} is named {plain(peer['name'], 80)} now: nothing was changed."
                    )
                requests.append((f"/api/wireguard/client/del_client/{quote(ref, safe='')}", {}, "deleted", which))
                continue
            wanted = {
                "name": change.get("name") or peer["name"],
                "public_key": key_of(change["public_key"], which) if change.get("public_key") else peer["public_key"],
                "addresses": [address_of(change["address"], which, networks)]
                if change.get("address")
                else peer["addresses"],
                "keepalive": peer["keepalive"] if change.get("keepalive") is None else max(change["keepalive"], 0),
            }
            if all(peer[field] == value for field, value in wanted.items()):
                requests.append(None)
                continue
            body = {
                "name": wanted["name"],
                "pubkey": wanted["public_key"],
                "tunneladdress": ",".join(wanted["addresses"]),
                "keepalive": str(wanted["keepalive"] or ""),
                # Every instance it is a peer of, kept: the relation is set whole by each write, so one left out
                # would take the peer off that instance.
                "servers": ",".join(peer["servers"]),
            }
            requests.append(
                (f"/api/wireguard/client/set_client/{quote(ref, safe='')}", {"client": body}, "updated", which)
            )
            # The addresses it gives up are free for a change after it: updates are made before adds.
            for address in peer["addresses"]:
                if address not in wanted["addresses"] and held.get(address) == ref:
                    del held[address]
        for address in wanted["addresses"]:
            holder = held.get(address)
            if holder is not None and holder != ref:
                if holder in by_ref:
                    by = f"is held by the peer {plain(by_ref[holder]['name'], 80)} of the instance"
                else:
                    by = f"is given by {holder} too"
                raise Refused(f"{which}: {address} {by}: nothing was changed.")
            held[address] = ref or f"change {number}"
    return requests


def apply(router, changes, instance, peers, check_mode, result, again=False):
    """Make the changes, deletes first; reconfigure once if anything was written, even when a later change failed, or
    when asked to (again): what is saved but not running, after a reconfigure that failed or never came."""
    requests = plan(changes, instance, peers)
    result["results"] = [
        {
            "kind": change["kind"],
            "name": change.get("name") or "",
            "ref": change.get("ref") or "",
            "result": "unchanged" if request is None else "not made",
        }
        for change, request in zip(changes, requests, strict=True)
    ]
    failure = None
    for index in sorted(range(len(changes)), key=lambda i: ORDER[changes[i]["kind"]]):
        request = requests[index]
        if request is None:
            continue
        path, body, outcome, which = request
        if not check_mode:
            try:
                answer = router.call("POST", path, body)
            except Refused as error:
                result["results"][index]["result"] = "refused"
                failure = error
                break
            if not done(answer, DELETED if outcome == "deleted" else SAVED):
                result["results"][index]["result"] = "refused"
                failure = Refused(f"{which} was refused by the router: {refusal(answer)}")
                break
            if outcome == "added":
                result["results"][index]["ref"] = str(answer.get("uuid") or "") if isinstance(answer, dict) else ""
            result["changed"] = True
        else:
            result["changed"] = True
        result["results"][index]["result"] = outcome
    if again and failure is None:
        result["changed"] = True
    if result["changed"] and not check_mode:
        try:
            answer = router.call("POST", "/api/wireguard/service/reconfigure", {})
        except Refused as error:
            answer, failure = None, failure or error
        if answer is not None:
            if done(answer, OK):
                result["reconfigured"] = True
            elif failure is None:
                failure = Refused(f"The changes were saved, but WireGuard's reconfigure answered: {refusal(answer)}")
    if failure is not None:
        raise failure


def run(router, params, check_mode, result):
    """Read, then apply under state=apply, filling result as it goes, so a failure returns what was found."""
    floor = numbers(params["floor"])
    if floor is None:
        raise Refused("floor is not a version, such as 26.7.2.")
    result["version"] = version_of(router)
    if older(numbers(result["version"]), floor):
        raise Refused(
            f"The router runs OPNsense {result['version']}, older than {plain(params['floor'], 40)}, the oldest this"
            " module talks to (WireGuard before 26.7.2 accepts forged packets, FreeBSD-SA-26:52): nothing else was"
            " asked of it, and nothing was changed."
        )
    instance, peers, listed = read(router, params["instance"])
    result["instance"], result["peers"] = public(instance, peers)
    result["listed"] = listed
    if params["state"] == "apply" and (params["changes"] or params["reconfigure"]):
        changes = [dict(change) for change in params["changes"]]
        apply(router, changes, instance, peers, check_mode, result, again=params["reconfigure"])
        if result["changed"] and not check_mode:
            instance, peers, result["listed"] = read(router, params["instance"])
            result["instance"], result["peers"] = public(instance, peers)
    result["handshakes"] = handshakes_of(router, instance)


def main():
    module = AnsibleModule(
        argument_spec={
            "url": {"type": "str", "required": True},
            "cert_sha256": {"type": "str", "required": True},
            "api_key": {"type": "str", "required": True, "no_log": True},
            "api_secret": {"type": "str", "required": True, "no_log": True},
            "instance": {"type": "str", "required": True},
            "floor": {"type": "str", "default": "26.7.2"},
            "state": {"type": "str", "choices": ["read", "apply"], "default": "read"},
            "changes": {
                "type": "list",
                "elements": "dict",
                "default": [],
                "options": {
                    "kind": {"type": "str", "required": True, "choices": ["add", "update", "delete"]},
                    "name": {"type": "str"},
                    "ref": {"type": "str"},
                    "public_key": {"type": "str"},
                    "address": {"type": "str"},
                    "keepalive": {"type": "int"},
                },
            },
            "reconfigure": {"type": "bool", "default": False},
            "timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=True,
    )
    params = module.params
    # A YAML block scalar leaves a newline after a secret: it is never part of one.
    key, secret = params["api_key"].strip(), params["api_secret"].strip()
    result = {
        "changed": False,
        "version": "",
        "instance": {},
        "peers": [],
        "listed": False,
        "handshakes": {},
        "results": [],
        "reconfigured": False,
    }
    try:
        router = Router(params["url"], pin_of(params["cert_sha256"]), key, secret, params["timeout"])
        # The header's form of them too, should anything ever echo the request.
        module.no_log_values.update(value for value in (key, secret, router.token) if value)
        run(router, params, module.check_mode, result)
    except Refused as error:
        module.fail_json(msg=plain(error, most=1000), **scrub(result))
    module.exit_json(**scrub(result))


if __name__ == "__main__":
    main()
