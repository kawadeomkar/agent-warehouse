#!/usr/bin/env python3
"""Stateful stand-in for the OPNsense API that playbooks/library/opnsense_wireguard.py calls, over TLS.

Its answers have the shapes captured from a real router (homeserver's WIREGUARD-PLAN §9.3), keys and addresses
replaced by placeholders:

  GET  /api/core/firmware/info                   product.product_version: 26.7.7 until a fixture says otherwise
  GET  /api/wireguard/server/search_server       the instances, each with its private key in plain text, as
  GET  /api/wireguard/server/get_server/<uuid>   OPNsense gives it; get_server's fields as option maps
  GET  /api/wireguard/client/search_client       the peers ("clients"), one with a stored private key (26.7's
  GET  /api/wireguard/client/get_client/<uuid>   peer generator) and one with a pre-shared key
  POST /api/wireguard/client/add_client          {"client": {...}}: {"result": "saved", "uuid": ...}, or
  POST /api/wireguard/client/set_client/<uuid>   {"result": "failed", "validations": {...}}
  POST /api/wireguard/client/del_client/<uuid>   {"result": "deleted"}, the peer gone from every instance's peers
  POST /api/wireguard/service/reconfigure        {"result": "ok"}, counted; what show reports is what it applied
  GET  /api/wireguard/service/show               interface and peer rows of what runs, handshakes as set
  GET  /api/wireguard/service/status             "unknown", as OPNsense says while WireGuard runs

A peer belongs to an instance through the instance's peers. A client's servers is a volatile relation (Client.xml's,
on 26.7): read, it is made from the instances' peers; written, it sets them whole - so a set_client without servers
takes the peer off every instance, the cautious reading of OPNsense's controller.

Validation failures come in both shapes a real router gives: a field's message as a string - an empty public key
("A value is required."), a name OPNsense's mask refuses, an address without a netmask, an instance that is not
one - and as a list - a public key another peer has ("Public keys should be unique."). A body without the model's
name, {"client": ...}, fails with no validations at all.

Basic authentication with the key and secret given; a request without them, or with others, is a 401.

Fixtures, outside OPNsense's API, never written to the calls log:

  POST /api/fake/version      {"version": "26.1.11_10"}
  POST /api/fake/server       {"name", "tunneladdress", "port"}: one more instance; its uuid
  POST /api/fake/handshake    {"public_key", "epoch"}
  POST /api/fake/reconfigure  {"answer": "result" | "status" | "text" | "failed"}: {"result": "ok"} (as captured),
                              {"status": "OK\\n\\n"}, OK and two newlines as plain text, or {"status": "failed"}
  POST /api/fake/listing      {"cap": N, "missing": M, "bare": B}: at most N rows a page whatever was asked; M peers
                              counted in search_client's total that never come; B, search_client without servers,
                              tunneladdress and pubkey, so each peer is read alone
  GET  /api/fake/state        the instances, the peers and how many reconfigures

Every other request is appended to <state_dir>/calls.log as a JSON line - its method, path, query, the body of a
POST, whether it carried an Authorization header and whether that was accepted - and the state is written to
<state_dir>/state.json after each change.

It never reaches a router: there is no network code but the listener.

Usage: fake_opnsense.py <port> <certfile> <keyfile> <state_dir> <api_key> <api_secret>
"""

import base64
import copy
import json
import re
import ssl
import sys
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PORT = int(sys.argv[1])
CERT, KEY = sys.argv[2], sys.argv[3]
STATE_DIR = Path(sys.argv[4])
API_KEY, API_SECRET = sys.argv[5], sys.argv[6]


def key(byte: int) -> str:
    """A WireGuard key of 32 bytes all `byte`: plainly fake, and of no entropy a secret scan would take for one."""
    return base64.b64encode(bytes([byte]) * 32).decode()


# The canned secrets: tests/unit/test_opnsense_wireguard.py looks for each of them in everything a run said.
INSTANCE_PRIVATE = {"homeserver": key(0x07), "other": key(0x08)}
PEER_PRIVATE = key(0x03)
PEER_PSK = key(0x05)

HOME = "5e1f0000-0000-4000-8000-000000000001"
OTHER = "5e1f0000-0000-4000-8000-000000000002"
LAPTOP = "c11e0000-0000-4000-8000-000000000011"
PHONE = "c11e0000-0000-4000-8000-000000000012"
ALPHA = "c11e0000-0000-4000-8000-000000000013"
OLD = "c11e0000-0000-4000-8000-000000000014"


def server(name: str, number: str, address: str, port: str, pubkey: str, peers: list) -> dict:
    return {
        "enabled": "1",
        "name": name,
        "instance": number,
        "pubkey": pubkey,
        "privkey": INSTANCE_PRIVATE.get(name, key(0x09)),
        "port": port,
        "mtu": "",
        "dns": "",
        "tunneladdress": [address],
        "disableroutes": "0",
        "gateway": "",
        "carp_depend_on": "",
        "peers": peers,
        "endpoint": "",
        "peer_dns": "",
        "allowed_ips": "",
    }


def client(name: str, pubkey: str, address: str, keepalive: str = "", privkey: str = "", psk: str = "") -> dict:
    return {
        "enabled": "1",
        "name": name,
        "pubkey": pubkey,
        "privkey": privkey,
        "psk": psk,
        "tunneladdress": [address],
        "serveraddress": "",
        "serverport": "",
        "endpoint": "",
        "keepalive": keepalive,
    }


# The instance the tests change, homeserver, with a device of the user's and two peers of agent-warehouse's;
# another instance with a device of its own.
SERVERS = {
    HOME: server("homeserver", "0", "198.51.100.1/24", "51820", key(0x51), [LAPTOP, ALPHA, OLD]),
    OTHER: server("other", "1", "203.0.113.1/24", "51821", key(0x52), [PHONE]),
}
CLIENTS = {
    LAPTOP: client("laptop", key(0x21), "198.51.100.2/32", privkey=PEER_PRIVATE, psk=PEER_PSK),
    PHONE: client("phone", key(0x22), "203.0.113.2/32"),
    ALPHA: client("agent-warehouse-alpha", key(0x23), "198.51.100.130/32", keepalive="25"),
    OLD: client("agent-warehouse-old", key(0x24), "198.51.100.140/32"),
}
HANDSHAKES = {key(0x21): 1793140625}
VERSION = {"version": "26.7.7"}
RECONFIGURE = {"answer": "result", "count": 0}
LISTING = {"cap": 0, "missing": 0, "bare": False}
# What WireGuard runs: the instances and peers as the last reconfigure found them.
RUNNING = {"servers": copy.deepcopy(SERVERS), "clients": copy.deepcopy(CLIENTS)}
# A name as OPNsense's model masks it, and an address with its netmask, which 26.7 requires.
NAME = re.compile(r"^[0-9a-zA-Z._\-]{1,64}$")
ADDRESS = re.compile(r"^[0-9a-fA-F.:]+/\d{1,3}$")


def save_state() -> None:
    STATE_DIR.joinpath("state.json").write_text(json.dumps(state(), indent=2, sort_keys=True))


def state() -> dict:
    return {"servers": SERVERS, "clients": CLIENTS, "reconfigures": RECONFIGURE["count"], "version": VERSION["version"]}


def servers_of(ref: str, servers: dict) -> list:
    return [server_ref for server_ref, record in servers.items() if ref in record["peers"]]


def server_row(ref: str, record: dict) -> dict:
    row = {"uuid": ref, **record}
    row["tunneladdress"] = ",".join(record["tunneladdress"])
    row["peers"] = ",".join(record["peers"])
    row["%peers"] = ",".join(CLIENTS[peer]["name"] for peer in record["peers"] if peer in CLIENTS)
    row["%carp_depend_on"] = "None"
    return row


def client_row(ref: str, record: dict) -> dict:
    row = {"uuid": ref, **record}
    refs = servers_of(ref, SERVERS)
    row["tunneladdress"] = ",".join(record["tunneladdress"])
    row["servers"] = ",".join(refs)
    row["%servers"] = ",".join(SERVERS[server_ref]["name"] for server_ref in refs)
    if LISTING["bare"]:
        for field in ("servers", "%servers", "tunneladdress", "pubkey"):
            row.pop(field)
    return row


def options(selected: list, every: dict) -> dict:
    """An option map, as get_server and get_client give a relation: every choice, the chosen ones selected."""
    return {ref: {"value": name, "selected": int(ref in selected)} for ref, name in every.items()}


def addresses(chosen: list) -> dict:
    return {address: {"value": address, "selected": 1} for address in chosen}


def page(rows: list, params: dict, missing: int = 0) -> dict:
    """A search's answer: every row, unless rowCount asks for a page or the fixture caps one."""
    try:
        count, current = int(params.get("rowCount", -1)), max(int(params.get("current", 1)), 1)
    except ValueError:
        count, current = -1, 1
    size = len(rows) if count <= 0 else count
    if LISTING["cap"]:
        size = min(size, LISTING["cap"]) if size else LISTING["cap"]
    start = (current - 1) * size
    chosen = rows[start : start + size]
    return {"rows": chosen, "rowCount": len(chosen), "total": len(rows) + missing, "current": current}


def show() -> dict:
    """service/show: an interface row for each instance that runs, a peer row for each of its peers."""
    rows = []
    servers, clients = RUNNING["servers"], RUNNING["clients"]
    for record in sorted(servers.values(), key=lambda item: item["instance"]):
        if record["enabled"] != "1":
            continue
        interface = f"wg{record['instance']}"
        rows.append(
            {
                "if": interface,
                "type": "interface",
                "public-key": record["pubkey"],
                "listen-port": record["port"],
                "fwmark": "off",
                "endpoint": record["port"],
                "status": "up",
                "name": record["name"],
                "latest-handshake-age": None,
                "latest-handshake-epoch": None,
                "peer-status": "offline",
                "ifname": record["name"],
            }
        )
        for peer in record["peers"]:
            if peer not in clients or clients[peer]["enabled"] != "1":
                continue
            shake = HANDSHAKES.get(clients[peer]["pubkey"], 0)
            rows.append(
                {
                    "if": interface,
                    "type": "peer",
                    "public-key": clients[peer]["pubkey"],
                    "endpoint": "192.0.2.50:36215" if shake else "(none)",
                    "allowed-ips": ",".join(clients[peer]["tunneladdress"]),
                    "latest-handshake": shake,
                    "transfer-rx": 7613 if shake else 0,
                    "transfer-tx": 13436 if shake else 0,
                    "persistent-keepalive": clients[peer]["keepalive"] or "off",
                    "name": clients[peer]["name"],
                    "latest-handshake-age": 33 if shake else None,
                    "latest-handshake-epoch": "2026-10-27 15:37:05" if shake else None,
                    "peer-status": "online" if shake else "offline",
                    "ifname": record["name"],
                }
            )
    return {"total": len(rows), "rowCount": len(rows), "current": 1, "rows": rows}


def validate(record: dict, ref: str, servers: list) -> dict:
    """OPNsense's validations of a client, as {field: message}: a string, or a list for a key already taken."""
    failed = {}
    if not record.get("pubkey"):
        failed["client.pubkey"] = "A value is required."
    elif any(other["pubkey"] == record["pubkey"] for other_ref, other in CLIENTS.items() if other_ref != ref):
        failed["client.pubkey"] = ["Public keys should be unique."]
    if not NAME.match(record.get("name") or ""):
        failed["client.name"] = "Should be a string between 1 and 64 characters, of letters, digits, '.', '_' or '-'."
    if not all(ADDRESS.match(address) for address in record.get("tunneladdress") or []):
        failed["client.tunneladdress"] = "Please specify a valid CIDR network, its netmask included."
    if any(server_ref not in SERVERS for server_ref in servers):
        failed["client.servers"] = "Option not in list."
    return failed


def write(ref: str, body: dict) -> dict:
    """add_client (ref None) or set_client: what the router answers, the change made only when it saves."""
    posted = body.get("client") if isinstance(body, dict) else None
    if not isinstance(posted, dict):
        return {"result": "failed"}
    record = copy.deepcopy(CLIENTS[ref]) if ref else client("", "", "")
    if not ref:
        record["tunneladdress"] = []
    for field in ("enabled", "name", "pubkey", "keepalive", "endpoint", "serveraddress", "serverport", "psk"):
        if field in posted:
            record[field] = str(posted[field])
    if "tunneladdress" in posted:
        record["tunneladdress"] = [part.strip() for part in str(posted["tunneladdress"]).split(",") if part.strip()]
    servers = [part.strip() for part in str(posted.get("servers") or "").split(",") if part.strip()]
    failed = validate(record, ref, servers)
    if failed:
        return {"result": "failed", "validations": failed}
    ref = ref or str(uuid.uuid4())
    CLIENTS[ref] = record
    for server_ref, instance in SERVERS.items():
        if server_ref in servers and ref not in instance["peers"]:
            instance["peers"].append(ref)
        elif server_ref not in servers and ref in instance["peers"]:
            instance["peers"].remove(ref)
    save_state()
    return {"result": "saved", "uuid": ref}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body, text: bool = False) -> None:
        payload = body.encode() if text else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if text else "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode(errors="replace") if length else ""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return raw

    def _accepted(self) -> bool:
        said = self.headers.get("Authorization", "")
        if not said.startswith("Basic "):
            return False
        try:
            pair = base64.b64decode(said[len("Basic ") :], validate=True).decode()
        except ValueError:
            return False
        return pair == f"{API_KEY}:{API_SECRET}"

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = {name: values[0] for name, values in parse_qs(parsed.query).items()}
        body = self._body() if method == "POST" else None
        accepted = self._accepted()
        if not path.startswith("/api/fake/"):
            with STATE_DIR.joinpath("calls.log").open("a") as log:
                entry = {
                    "method": method,
                    "path": path,
                    "query": params,
                    "body": body,
                    "auth": "Authorization" in self.headers,
                    "accepted": accepted,
                }
                log.write(json.dumps(entry) + "\n")
        if not accepted:
            self._send(401, {"status": 401, "message": "Authentication Failed"})
            return
        if isinstance(body, dict):
            # A search reads its paging from the query or from a JSON body.
            params = {**{name: str(value) for name, value in body.items() if not isinstance(value, dict)}, **params}
        parts = [unquote(part) for part in path.strip("/").split("/")]
        if parts[:2] == ["api", "fake"]:
            self._fixture(method, parts[2:], body if isinstance(body, dict) else {})
        else:
            self._api(method, parts[1:], params, body)

    def _fixture(self, method: str, parts: list, body: dict) -> None:
        if parts == ["state"]:
            self._send(200, state())
        elif parts == ["version"] and method == "POST":
            VERSION["version"] = str(body["version"])
            self._send(200, dict(VERSION))
        elif parts == ["server"] and method == "POST":
            ref = str(uuid.uuid4())
            number = str(len(SERVERS))
            SERVERS[ref] = server(body["name"], number, body["tunneladdress"], str(body.get("port", "")), key(0x50), [])
            save_state()
            self._send(200, {"uuid": ref})
        elif parts == ["handshake"] and method == "POST":
            HANDSHAKES[body["public_key"]] = int(body["epoch"])
            self._send(200, HANDSHAKES)
        elif parts == ["reconfigure"] and method == "POST":
            RECONFIGURE["answer"] = body["answer"]
            self._send(200, RECONFIGURE)
        elif parts == ["listing"] and method == "POST":
            LISTING.update({name: body[name] for name in ("cap", "missing", "bare") if name in body})
            self._send(200, LISTING)
        else:
            self._send(404, {"errorMessage": "Endpoint not found"})

    def _api(self, method: str, parts: list, params: dict, body) -> None:
        if parts == ["core", "firmware", "info"]:
            product = {
                "product_name": "OPNsense",
                "product_version": VERSION["version"],
                "product_latest": "26.7.7",
                "product_series": ".".join(VERSION["version"].split(".")[:2]),
            }
            self._send(200, {"product": product, "package": [], "plugin": [], "changelog": []})
        elif parts[:1] != ["wireguard"]:
            self._send(404, {"errorMessage": "Endpoint not found"})
        elif parts == ["wireguard", "server", "search_server"]:
            self._send(200, page([server_row(ref, record) for ref, record in SERVERS.items()], params))
        elif parts[:3] == ["wireguard", "server", "get_server"] and len(parts) == 4:
            record = SERVERS.get(parts[3])
            if record is None:
                self._send(200, [])
                return
            shown = {**record, "tunneladdress": addresses(record["tunneladdress"])}
            shown["peers"] = options(record["peers"], {ref: peer["name"] for ref, peer in CLIENTS.items()})
            shown["dns"] = {"": {"value": "", "selected": 1}}
            shown["carp_depend_on"] = {"": {"value": "None", "selected": 1}}
            self._send(200, {"server": shown})
        elif parts == ["wireguard", "client", "search_client"]:
            rows = [client_row(ref, record) for ref, record in CLIENTS.items()]
            self._send(200, page(rows, params, LISTING["missing"]))
        elif parts[:3] == ["wireguard", "client", "get_client"] and len(parts) == 4:
            record = CLIENTS.get(parts[3])
            if record is None:
                self._send(200, [])
                return
            shown = {**record, "tunneladdress": addresses(record["tunneladdress"])}
            every = {ref: instance["name"] for ref, instance in SERVERS.items()}
            shown["servers"] = options(servers_of(parts[3], SERVERS), every)
            self._send(200, {"client": shown})
        elif parts == ["wireguard", "client", "add_client"]:
            self._send(200, write(None, body) if method == "POST" else {"result": "failed"})
        elif parts[:3] == ["wireguard", "client", "set_client"] and len(parts) == 4:
            if method != "POST" or parts[3] not in CLIENTS:
                self._send(200, {"result": "failed"})
            else:
                self._send(200, write(parts[3], body))
        elif parts[:3] == ["wireguard", "client", "del_client"] and len(parts) == 4:
            if method != "POST" or parts[3] not in CLIENTS:
                self._send(200, {"result": "not found"})
                return
            del CLIENTS[parts[3]]
            for instance in SERVERS.values():
                if parts[3] in instance["peers"]:
                    instance["peers"].remove(parts[3])
            save_state()
            self._send(200, {"result": "deleted"})
        elif parts == ["wireguard", "service", "reconfigure"]:
            self._reconfigure(method)
        elif parts == ["wireguard", "service", "show"]:
            self._send(200, show())
        elif parts == ["wireguard", "service", "status"]:
            self._send(200, {"status": "unknown", "widget": {"caption_restart": "Restart"}})
        else:
            self._send(404, {"errorMessage": "Endpoint not found"})

    def _reconfigure(self, method: str) -> None:
        if method != "POST" or RECONFIGURE["answer"] == "failed":
            self._send(200, {"status": "failed"})
            return
        RECONFIGURE["count"] += 1
        RUNNING["servers"], RUNNING["clients"] = copy.deepcopy(SERVERS), copy.deepcopy(CLIENTS)
        save_state()
        if RECONFIGURE["answer"] == "status":
            self._send(200, {"status": "OK\n\n"})
        elif RECONFIGURE["answer"] == "text":
            self._send(200, "OK\n\n", text=True)
        else:
            self._send(200, {"result": "ok"})

    def do_GET(self):  # http.server API name
        self._handle("GET")

    def do_POST(self):  # http.server API name
        self._handle("POST")

    def log_message(self, *args):
        pass


STATE_DIR.mkdir(parents=True, exist_ok=True)
save_state()
httpd = HTTPServer(("127.0.0.1", PORT), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(CERT, KEY)
httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
# Given port 0 the system picks a free one, said on stdout: tests/unit/harness.py starts fakes so,
# never on a port it probed and freed, which another xdist worker's fake could take first.
if PORT == 0:
    print(httpd.server_address[1], flush=True)
httpd.serve_forever()
