"""Filters for just init: what an answer must look like, and writing one into a settings file's text.

Each question of playbooks/init_settings.yml has a kind, and an answer of that
kind is accepted in one normal form or refused. A value typed at a prompt and
one given on just init's command line go through the same filter, so both are held
to the same rule before they reach a file.
"""

from __future__ import annotations

import ipaddress
import json
import re

import yaml
from ansible.errors import AnsibleFilterError

# The documentation address hosts.yml.example ships with (RFC 5737), never a
# Proxmox host's.
EXAMPLE_HOST = "192.0.2.10"
_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
_TOKEN_ID = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z][A-Za-z0-9._-]*![A-Za-z][A-Za-z0-9._-]*")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
# Tailscale's tskey-auth-..., an OAuth client secret with its ?ephemeral=false,
# or a key of the person's own control server (Headscale's are plain hex): one
# word, never a prefix of Tailscale's alone. Never an API access token, which the
# same Keys page makes and which joins nothing.
_AUTH_KEY = re.compile(r"(?!tskey-api-)[A-Za-z0-9._:/?=&+-]{20,512}")
# What Proxmox itself allows as the name of a node (a host name's first label),
# a storage (pve-storage-id) and a bridge (a network interface's name). just
# init writes such a name into local.yml, which every later run reads as a
# template: a name the API or root SSH reported is held to these first, so
# one that holds anything else - {{ ... }} above all - is never offered or
# written, whoever answered for the host.
PROXMOX_NAMES = {
    "node": _LABEL,
    "storage": re.compile(r"(?i)[a-z][a-z0-9_.-]*[a-z0-9]"),
    "bridge": re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}"),
}

RULES = {
    "host": (
        "an IP address or a host name (letters, digits and hyphens, in labels separated by dots), without"
        f" https:// or a port, and not the example address {EXAMPLE_HOST}"
    ),
    "token_id": "a token ID, user@realm!name, as pveum printed it: ansible@pve!ansible, say",
    "uuid": "the 36-character secret pveum printed, like 01234567-89ab-cdef-0123-456789abcdef",
    "github_login": (
        "a GitHub username: at most 39 letters, digits and single hyphens, not starting or ending with a hyphen"
    ),
    "auth_key": (
        "a key as Tailscale printed it (tskey-auth-..., or an OAuth client secret, tskey-client-..., which needs"
        " tailscale_advertise_tags) or your control server did, never an API access token (tskey-api-...): one word"
        " of at least 20 letters, digits and . _ : / ? = & + -"
    ),
    "yes_no": "y or n",
    "choice": "one of the numbers listed",
}


def _host(text):
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        address = None
    if address is not None:
        # An IPv6 address with a zone (fe80::1%en0) parses, but a zone is this
        # machine's and means nothing in a URL the API is reached at.
        if "%" in text:
            return ""
        return "" if str(address) == EXAMPLE_HOST else str(address)
    labels = text.split(".")
    if len(text) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        return ""
    # A last label of digits alone makes a malformed IP address, not a name.
    return "" if labels[-1].isdigit() else text


def init_answer(value, kind, choices=None):
    """Return ``value`` in the normal form of ``kind``, or ``""`` when it is not one.

    host: an address or a DNS name, as typed (an IP address in its compressed
    form). token_id, github_login and auth_key: as typed. uuid: lowercased. yes_no:
    ``y`` or ``n``. choice: the item of ``choices`` the number names. Leading
    and trailing whitespace is never part of an answer. An empty value is
    ``""``, the answer that decides later.
    """
    if kind not in RULES:
        raise AnsibleFilterError(f"init_answer: no kind {kind!r}; one of {', '.join(RULES)}")
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if kind == "host":
        return _host(text)
    if kind == "token_id":
        return text if _TOKEN_ID.fullmatch(text) else ""
    if kind == "uuid":
        text = text.lower()
        return text if _UUID.fullmatch(text) else ""
    if kind == "github_login":
        return text if _GITHUB_LOGIN.fullmatch(text) else ""
    if kind == "auth_key":
        return text if _AUTH_KEY.fullmatch(text) else ""
    if kind == "yes_no":
        return {"y": "y", "yes": "y", "n": "n", "no": "n"}.get(text.lower(), "")
    items = list(choices or [])
    if text.isdigit() and 1 <= int(text) <= len(items):
        return items[int(text) - 1]
    return ""


def proxmox_name(value, kind):
    """Return ``value`` when it is a name Proxmox gives a ``kind`` (node, storage, bridge), else ``""``."""
    if kind not in PROXMOX_NAMES:
        raise AnsibleFilterError(f"proxmox_name: no kind {kind!r}; one of {', '.join(PROXMOX_NAMES)}")
    return value if isinstance(value, str) and PROXMOX_NAMES[kind].fullmatch(value) else ""


def proxmox_names(values, kind):
    """The items of ``values`` that are names Proxmox gives a ``kind``, in their order."""
    return [value for value in values or [] if proxmox_name(value, kind)]


def proxmox_names_refused(values, kind):
    """The items of ``values`` that are not, each as JSON, so whatever it holds is shown and never acted on."""
    return [json.dumps(str(value)) for value in values or [] if not proxmox_name(value, kind)]


def init_answer_rule(kind):
    """The rule an answer of ``kind`` must follow, in words, for the prompt that asks again."""
    if kind not in RULES:
        raise AnsibleFilterError(f"init_answer_rule: no kind {kind!r}; one of {', '.join(RULES)}")
    return RULES[kind]


def with_setting(text, key, value, placeholder=""):
    """Return YAML ``text`` with top-level ``key`` set to ``value``, written as JSON.

    Only a line that leaves the key empty or holds ``placeholder`` - the value
    the example file ships with - is replaced, in place, trailing comment and
    all; a text without the key gets the line at its end. A key set to
    anything else raises: just init writes only what is not set yet, and the
    caller has already said so.
    """
    pattern = re.compile(rf"^{re.escape(key)}:(?P<rest>.*)$", re.MULTILINE)
    line = f"{key}: {json.dumps(value)}"
    written = None
    for match in pattern.finditer(text):
        try:
            current = yaml.safe_load(match.group("rest") or "")
        except yaml.YAMLError:
            current = object()
        if current not in (None, "", placeholder):
            raise AnsibleFilterError(f"with_setting: {key} is already set; just init writes only a value not set yet")
        written = text[: match.start()] + line + text[match.end() :]
        break
    if written is None:
        written = text + ("" if not text or text.endswith("\n") else "\n") + line + "\n"
    # A value the line did not hold whole - a block scalar's first line, say -
    # leaves text that no longer parses, or reads back otherwise: never written.
    try:
        parsed = yaml.safe_load(written)
    except yaml.YAMLError:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get(key) != value:
        raise AnsibleFilterError(f"with_setting: {key} cannot be written into this file in place: use just vault")
    return written


def _size(value):
    """Bytes as the Proxmox UI writes them: 412 GiB, 1.8 TiB."""
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:.0f} {unit}" if size >= 10 or unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def init_candidates(results, key, prefix, required):
    """What the token can use among a node's storages or bridges, from the permission reads made for each.

    ``results`` are the results of a looped ``uri`` of /access/permissions,
    one per listing entry, each with the entry as its ``item``. ``key`` names
    the entry's name (``storage``, ``iface``), ``prefix`` its permission path
    (``/storage/``), and ``required`` the privileges it takes. Each candidate
    is ``{"name", "type", "free", "lacks", "inactive"}``, in the listing's
    order: ``lacks`` lists the required privileges the token does not hold
    there, and ``inactive`` is a storage Proxmox lists but cannot reach (an NFS
    share that is down). A candidate is usable with neither. A permission read
    that failed lacks everything. A name Proxmox would not give a storage or a
    bridge is ``refused``: shown as JSON, never usable, whatever the token holds.
    """
    kind = "storage" if key == "storage" else "bridge"
    candidates = []
    for result in results or []:
        entry = result.get("item") or {}
        name = str(entry.get(key, ""))
        refused = not proxmox_name(name, kind)
        data = (result.get("json") or {}).get("data") or {}
        held = data.get(prefix + name) or {} if result.get("status") == 200 and not refused else {}
        candidates.append(
            {
                "name": json.dumps(name) if refused else name,
                "type": str(entry.get("type", "")) if not refused else "",
                "free": _size(entry["avail"]) + " free" if "avail" in entry and not refused else "",
                "lacks": [privilege for privilege in required if privilege not in held],
                "inactive": str(entry.get("active", 1)) == "0",
                "refused": refused,
            }
        )
    return candidates


def init_choose_rows(candidates, path):
    """The lines a choice shows: the usable candidates numbered, then the others with what the token lacks."""
    usable = [c for c in candidates if not c["lacks"] and not c.get("inactive") and not c.get("refused")]
    unusable = [c for c in candidates if c not in usable]
    name = max((len(c["name"]) for c in candidates), default=0)
    kind = max((len(c["type"]) for c in candidates), default=0)
    lines = []
    for number, c in enumerate(usable, 1):
        prefix = f"{number}) " if len(usable) > 1 else "-  "
        lines.append(f"  {prefix}{c['name']:<{name}}   {c['type']:<{kind}}   {c['free']}".rstrip())
    for c in unusable:
        why = (
            "not a name Proxmox gives one, so never used"
            if c.get("refused")
            else f"the token lacks {', '.join(c['lacks'])} on {path}{c['name']}"
            if c["lacks"]
            else "inactive"
        )
        lines.append(f"  -  {c['name']:<{name}}   {c['type']:<{kind}}   {why}")
    return lines


class FilterModule:
    def filters(self):
        return {
            "init_answer": init_answer,
            "init_answer_rule": init_answer_rule,
            "with_setting": with_setting,
            "init_candidates": init_candidates,
            "init_choose_rows": init_choose_rows,
            "proxmox_name": proxmox_name,
            "proxmox_names": proxmox_names,
            "proxmox_names_refused": proxmox_names_refused,
        }
