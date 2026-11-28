"""Stateful stand-in for the Proxmox VE commands tools/proxmox-access.sh runs: pveum, pvesh, pveversion, hostname, id.

Run as `fake_pve_cli.py <state.json> <command> <args...>`, through a wrapper of
each command's name (install() writes them). It keeps the host in one JSON file
- the version, the node, its storages and bridges, the roles, users, ACLs,
tokens and pools - answers only the calls the script makes, as Proxmox answers
them, and appends every call to calls.log beside the state. A call whose words
contain the state's "fail" string fails, as a refused write would. One call at
a time holds the state, by a lock on a file beside it, as Proxmox holds user.cfg
under its cluster's lock; a call writes the state only where it changed it, and
whole, by a rename, so a read writes nothing and no call meets half a state.

It answers too what the new engine's init will run there, as Proxmox VE 9 does
(pve-access-control's PVE/API2/User.pm, ACL.pm, Role.pm and AccessControl.pm,
pve-manager's PVE/API2/Pool.pm, pve-common's JSONSchema.pm and CLIFormatter.pm):
a pool made with a comment, given one with `pveum pool modify --comment`, and
read with it by `pvesh get /pools` (with --poolid, or the deprecated
/pools/<pool>, its members too); an ACL entry for a token (`pveum acl modify
<path> --tokens <user@realm!name>`); a token made with --privsep 1 and its
secret made again (`pveum user token modify --regenerate`); and the reads that
say who made an object: a role's privileges, a user's and a token's comment, a
token's privsep. The state keeps what Proxmox keeps in user.cfg, which it reads
again at every call: a comment only where it is not empty, and no ACL entry of
a token that is gone. A token is found by its full ID, its user's and its own
name, so two users' tokens of one name stay apart.

What it prints is what Proxmox prints. pvesh answers compact JSON, the one
format it takes. pveum's answers (user permissions, token add, token modify)
are the bordered table pveum draws for its default format, text, as a host
whose locale is UTF-8 draws it, unless --output-format json asks for compact
JSON; json-pretty and yaml are refused as unsupported. Options are read as
Proxmox's command line reads them (PVE::JSONSchema's get_options): a boolean
(BOOLEANS) given bare is 1, and 1, on, yes or true, or 0, off, no or false, in
any case; any other option takes the next word as its value, one that starts
with -- too, and one given no value is refused. A user's ID and a token's are
held to Proxmox's patterns wherever one is given, and an ACL entry's path is
normalised as Proxmox normalises it and refused where Proxmox refuses it.

Every refusal says what Proxmox says, whole, in the order Proxmox checks: a
parameter Proxmox's schema refuses first, the 400 with each parameter's words
(pveum's usage line after it left out); then what its code refuses, with the
label of its lock on user.cfg where it refuses inside it (`create role failed:
role 'x' already exists`), and with none where it refuses before it (`no such
user ('x')`). The stand-in's own words are for what it does not model alone:
an option a command does not take here (PVEUM_OPTIONS, and each pvesh read's
own), or a call it does not take, is refused as an unsupported call, never
ignored, and a read it does not know as no such path. One answer is today's
and not Proxmox's: `acl delete` refuses a delete that removes nothing.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

# The privileges each version knows, enough to refuse the one it does not:
# Proxmox VE 9 dropped VM.Monitor for VM.GuestAgent.*.
COMMON = (
    "VM.Allocate VM.Clone VM.Config.CPU VM.Config.Cloudinit VM.Config.Disk VM.Config.Memory VM.Config.Network"
    " VM.Config.Options VM.Config.HWType VM.Config.CDROM VM.PowerMgmt VM.Audit VM.Console VM.Backup VM.Snapshot"
    " VM.Migrate Datastore.AllocateSpace Datastore.Audit Datastore.Allocate SDN.Use SDN.Audit SDN.Allocate"
    " Sys.Audit Sys.Modify Permissions.Modify User.Modify Pool.Audit Pool.Allocate"
).split()
BY_MAJOR = {8: ["VM.Monitor"], 9: ["VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted"]}

# The options each pveum command takes here, as Proxmox names them: any other is refused as an unsupported call, never
# ignored, which would answer as though Proxmox had done what it asked. A token is made with what today's script and
# the new engine's init pass, and changed only to make its secret again, the one change a card asks of it.
PVEUM_OPTIONS = {
    ("role", "add"): {"privs"},
    ("role", "modify"): {"privs", "append"},
    ("pool", "add"): {"comment"},
    ("pool", "modify"): {"vms", "allow-move", "comment"},
    ("acl", "modify"): {"roles", "users", "tokens", "propagate"},
    ("acl", "delete"): {"roles", "users"},
    ("user", "add"): {"comment"},
    ("user", "permissions"): {"path", "output-format"},
    ("user", "token", "add"): {"privsep", "comment", "output-format"},
    ("user", "token", "modify"): {"regenerate", "output-format"},
    ("user", "token", "delete"): set(),
    ("user", "token", "remove"): set(),
}
# The options of these commands that are booleans, which Proxmox's command line takes given bare (Getopt::Long's
# `name:s`), and the output's own (RESTHandler's standard_output_options); every other option is a string, which takes
# the next word as its value whatever it is (`name=s`).
BOOLEANS = {"privsep", "propagate", "append", "allow-move", "regenerate", "enabled", "noborder", "noheader", "quiet"}
# The formats --output-format takes (pve-common's pve-output-format): the stand-in prints text and json.
FORMATS = ("text", "json", "json-pretty", "yaml")

# A realm's name, which a token's ID is held to as well, two characters at least (pve-access-control's
# PVE/Auth/Plugin.pm, realm_regex, and PVE/AccessControl.pm, token_subid_regex); a user's ID, name@realm, the name
# anything but white space, : and /; a token's full ID, user@realm!tokenid.
REALM = r"[A-Za-z][A-Za-z0-9.\-_]+"
USER = rf"[^\s:/]+@{REALM}"
FULL_TOKEN = rf"{USER}!{REALM}"
# The roles Proxmox makes for itself whose names are outside the PVE namespace (PVE/AccessControl.pm's special_roles),
# which a role add finds there already.
SPECIAL_ROLES = ("NoAccess", "Administrator")
# The paths an ACL entry may be on, once normalised (PVE/AccessControl.pm's check_path): any other is refused.
_NAME = r"[A-Za-z0-9.\-_]+"
_POOL = r"[A-Za-z0-9.\-_]+(?:/[A-Za-z0-9.\-_]+){0,2}"
ACL_PATHS = "|".join(
    (
        "/",
        "/access",
        "/access/groups",
        f"/access/groups/{_NAME}",
        "/access/realm",
        f"/access/realm/{_NAME}",
        "/nodes",
        f"/nodes/{_NAME}",
        f"/nodes/{_NAME}/certificates",
        "/pool",
        f"/pool/{_POOL}",
        "/sdn",
        "/sdn/controllers",
        r"/sdn/controllers/[A-Za-z0-9_\-]+",
        *(f"/sdn/{kind}" for kind in ("dns", "fabrics", "ipams", "prefix-lists", "route-maps")),
        *(f"/sdn/{kind}/[A-Za-z0-9]+" for kind in ("dns", "fabrics", "ipams", "prefix-lists", "route-maps")),
        "/sdn/zones",
        f"/sdn/zones/{_NAME}",
        f"/sdn/zones/{_NAME}/{_NAME}",
        f"/sdn/zones/{_NAME}/{_NAME}/[1-9][0-9]{{0,3}}",
        "/storage",
        f"/storage/{_NAME}",
        "/vms",
        "/vms/[1-9][0-9]{2,}",
        "/mapping",
        f"/mapping/{_NAME}",
        f"/mapping/{_NAME}/{_NAME}",
    )
)
# The borders of a table as pveum draws one where the host's locale is UTF-8 (PVE::CLIFormatter's print_text_table):
# above it, under its header, between its rows and below it, each its left end, its line, a joint and its right end.
BORDERS = {
    "top": ("┌", "─", "┬", "┐"),
    "header": ("╞", "═", "╪", "╡"),
    "row": ("├", "─", "┼", "┤"),
    "bottom": ("└", "─", "┴", "┘"),
}

DEFAULT = {
    "version": "9.2.13",
    "node": "pve",
    "uid": 0,
    "storages": [
        {"storage": "local-lvm", "type": "lvmthin", "content": "images,rootdir"},
        {"storage": "local", "type": "dir", "content": "iso,vztmpl,backup"},
    ],
    "bridges": ["vmbr0"],
    "roles": {"PVEAuditor": ["VM.Audit", "Datastore.Audit", "SDN.Audit", "Sys.Audit"]},
    "users": {"root@pam": {}},
    "acls": [],
    "tokens": {},
    "pools": {},
    # The guests on the host, as /cluster/resources lists them: {vmid, name, type, template, tags, pool}.
    "guests": [],
    "fail": "",
}


def install(directory: Path, state: dict | None = None) -> Path:
    """Write the state and a wrapper per command into directory; returns it, to put first on PATH."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pve-state.json").write_text(json.dumps(DEFAULT | (state or {})))
    for name in ("pveum", "pvesh", "pveversion", "hostname", "id"):
        wrapper = directory / name
        wrapper.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(__file__)} "
            f'"$(dirname "$0")/pve-state.json" {name} "$@"\n'
        )
        wrapper.chmod(0o755)
    return directory


def _major(state: dict) -> int:
    return int(state["version"].split(".")[0])


def _known(state: dict) -> set:
    return {*COMMON, *BY_MAJOR.get(_major(state), [])}


def _options(words: list[str]) -> tuple[list[str], dict]:
    """Positional words, and --name value pairs, as Proxmox's command line reads them (PVE::JSONSchema's get_options,
    through Getopt::Long): a boolean takes the next word unless that is another --name or there is none, given bare
    the value ""; any other option takes the next word whatever it is, one that starts with -- too, and has the value
    None where there is none, which get_options refuses."""
    positional, options, index = [], {}, 0
    while index < len(words):
        if not words[index].startswith("--"):
            positional.append(words[index])
            index += 1
            continue
        name, following = words[index][2:], words[index + 1 : index + 2]
        if name in BOOLEANS and (not following or following[0].startswith("--")):
            options[name], index = "", index + 1
        else:
            options[name], index = (following or [None])[0], index + 2
    return positional, options


def _boolean(options: dict, name: str, default: int) -> int | None:
    """A boolean option as Proxmox reads one (PVE::JSONSchema's parse_boolean): given bare, 1; 1, on, yes or true, and
    0, off, no or false, in any case; None for any other word, which it refuses."""
    if name not in options:
        return default
    word = options[name].lower()
    if word in ("", "1", "on", "yes", "true"):
        return 1
    return 0 if word in ("0", "off", "no", "false") else None


def _split(value: str | None) -> list[str]:
    """A list as Proxmox's PVE::Tools::split_list reads one: the words between commas, semicolons and white space."""
    return [word for word in re.split(r"[,;\s]+", value or "") if word]


def _comment(given: dict) -> dict:
    """The comment of what given says, as Proxmox keeps it: only where it is not empty, since it reads user.cfg again
    at every call and an empty comment there is none (PVE::AccessControl's parse_user_config)."""
    return {"comment": given["comment"]} if given.get("comment") else {}


def _token_info(token: dict) -> dict:
    """A token as Proxmox reads it back: its expire, 0 unless set, its privsep, and its comment where it has one."""
    return {"expire": token.get("expire", 0), "privsep": token["privsep"], **_comment(token)}


def _pool(name: str, pool: dict) -> dict:
    """A pool as Proxmox lists one: its ID, and its comment where it has one."""
    return {"poolid": name, **_comment(pool)}


def _members(state: dict, name: str) -> list[dict]:
    """A pool's guests, as Proxmox lists a pool's members (pve-manager's PVE/API2/Pool.pm and PVE/API2Tools.pm,
    extract_vm_stats): in the order of their VMIDs as text (`sort keys`, so 1000 before 999), each its ID, VMID, node,
    type and status, and the name and template flag its statistics carry; never its pool or its tags, which only
    /cluster/resources names."""
    members = []
    for guest in sorted((g for g in state.get("guests", []) if g.get("pool") == name), key=lambda g: str(g["vmid"])):
        kind = guest.get("type", "qemu")
        entry = {"id": f"{kind}/{guest['vmid']}", "vmid": guest["vmid"], "node": state["node"], "type": kind}
        members.append(
            entry | {"status": "stopped"} | {key: guest[key] for key in ("name", "template") if key in guest}
        )
    return members


def _held(state: dict, user: str, path: str) -> dict:
    """The privileges user holds on path, as Proxmox works them out: from / down to the path, the roles of the deepest
    level that grants the user any - on the path itself, or above it and propagating - take the place of those above,
    and NoAccess among them leaves none. A group's grant is no user's here: the state keeps no memberships."""
    words = [word for word in path.split("/") if word]
    levels = ["/", *("/" + "/".join(words[: count + 1]) for count in range(len(words)))]
    roles: dict = {}
    for level in levels:
        own = {
            acl["roleid"]: acl["propagate"]
            for acl in state["acls"]
            if acl["ugid"] == user
            and acl.get("type", "user") == "user"
            and (acl["path"].rstrip("/") or "/") == level
            and (level == levels[-1] or acl["propagate"])
        }
        roles = own or roles
    if "NoAccess" in roles:
        return {}
    return {privilege: propagate for role, propagate in roles.items() for privilege in state["roles"].get(role, [])}


def _acl_path(path: str) -> str | None:
    """An ACL entry's path as Proxmox keeps it (PVE/AccessControl.pm's normalize_path and check_path): doubled / taken
    as one, a trailing / dropped, a leading one added; None for one with another character, or one check_path does not
    know."""
    if not path:
        return None
    normal = re.sub("/+", "/", path).removesuffix("/") or "/"
    normal = normal if normal.startswith("/") else f"/{normal}"
    if not re.fullmatch(r"[A-Za-z0-9.\-_/]+", normal) or not re.fullmatch(f"(?:{ACL_PATHS})", normal):
        return None
    return normal


def _user_refused(userid: str) -> str | None:
    """Why a user's ID is not one Proxmox takes, in its words (PVE/Auth/Plugin.pm's verify_username); None where it
    is."""
    if len(userid) < 3:
        return f"user name '{userid}' is too short"
    if len(userid) > 64:
        return f"user name '{userid}' is too long ({len(userid)} > 64)"
    return None if re.fullmatch(USER, userid) else f"value '{userid}' does not look like a valid user name"


def _refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _refuse_parameters(errors: dict) -> int:
    """A parameter Proxmox refuses, as its command line says one: the 400, then each parameter's words."""
    return _refuse("\n".join(["400 Parameter verification failed.", *(f"{k}: {v}" for k, v in sorted(errors.items()))]))


def _unmodelled(options: dict, known: set, command: str, words: list[str]) -> int | None:
    """The refusal of an option the call does not take here, as an unsupported call; None where it takes them all."""
    return _refuse(f"fake {command}: unsupported call {words}") if set(options) - known else None


def _unparsed(options: dict) -> int | None:
    """The refusal of an option Proxmox's command line cannot read, before anything else: a string given no value,
    which Getopt::Long says first, or a boolean no word of parse_boolean's; None where each is read."""
    bare = sorted(name for name, value in options.items() if value is None)
    if bare:
        return _refuse(f"Option {bare[0]} requires an argument\n400 unable to parse option")
    if any(_boolean(options, name, 0) is None for name in BOOLEANS & set(options)):
        return _refuse("400 unable to parse boolean option")
    return None


def _ids_refused(errors: dict, user: str, token: str | None = None) -> dict:
    """errors with what Proxmox's schema says of a user's ID given as `userid` (its format, pve-userid) and of a token's
    ID given as `tokenid` (its pattern, token-subid's)."""
    refused = _user_refused(user)
    if refused:
        errors["userid"] = f"invalid format - {refused}"
    if token is not None and not re.fullmatch(REALM, token):
        errors["tokenid"] = "value does not match the regex pattern"
    return errors


def _parameters_refused(command: tuple, args: list[str], options: dict, state: dict) -> dict:
    """What Proxmox's schema refuses of a pveum call's parameters, before its code runs (pve-common's RESTHandler and
    JSONSchema's validate), each parameter's words: a user's ID and a token's, the user or full token ID whose
    permissions are read (a pattern), the privileges a role is given (pve-priv-list), an ACL entry's roles (required),
    users (pve-userid-list) and tokens (pve-tokenid-list), and the output's format; {} where it takes them all."""
    errors: dict = {}
    if command in (("user", "add"), *(key for key in PVEUM_OPTIONS if key[:2] == ("user", "token"))) and args:
        _ids_refused(errors, args[0], args[1] if command[:2] == ("user", "token") and len(args) > 1 else None)
    if command == ("user", "permissions") and args and not re.fullmatch(rf"{USER}(?:!{REALM})?", args[0]):
        errors["userid"] = "value does not match the regex pattern"
    if command in (("role", "add"), ("role", "modify")):
        unknown = [privilege for privilege in _split(options.get("privs")) if privilege not in _known(state)]
        if unknown:
            errors["privs"] = f"invalid format - invalid privilege '{unknown[0]}'"
    if command == ("acl", "modify"):
        if "roles" not in options:
            errors["roles"] = "property is missing and it is not optional"
        refused = next(filter(None, map(_user_refused, _split(options.get("users")))), None)
        if refused:
            errors["users"] = f"invalid format - {refused}"
        token = next((t for t in _split(options.get("tokens")) if not re.fullmatch(FULL_TOKEN, t)), None)
        if token is not None:
            errors["tokens"] = f"invalid format - value '{token}' does not look like a valid token ID"
    if options.get("output-format", "text") not in FORMATS:
        given = options["output-format"]
        errors["output-format"] = f"value '{given}' does not have a value in the enumeration '{', '.join(FORMATS)}'"
    return errors


def _token(state: dict, user: str, name: str, label: str = "") -> tuple[dict | None, int | None]:
    """A user's token, found as check_token_exist finds one, by its user and its name; or None and the refusal Proxmox
    gives, its user's first, with the label of the lock it is asked under."""
    if user not in state["users"]:
        return None, _refuse(f"{label}no such user ('{user}')")
    token = state["tokens"].get(f"{user}!{name}")
    if token is None:
        return None, _refuse(f"{label}no such token '{name}' for user '{user}'")
    return token, None


def _json(data: object) -> str:
    """An answer as Proxmox's --output-format json prints one: compact, its keys sorted."""
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _table(titles: list[str], rows: list[list[str]]) -> list[str]:
    """The lines of a table as pveum draws one in its text format (PVE::CLIFormatter's print_text_table, with borders
    and its header, the locale UTF-8): each column as wide as its widest line and its text to the left, a cell of
    several lines as many lines high, and a border between rows."""
    cells = [[cell.split("\n") if cell else [] for cell in row] for row in rows]
    widths = [
        max([len(title), *(len(line) for row in cells for line in row[column])]) for column, title in enumerate(titles)
    ]

    def border(kind: str) -> str:
        left, line, joint, right = BORDERS[kind]
        return left + joint.join(line * (width + 2) for width in widths) + right

    def line(texts: list[str]) -> str:
        return "│" + "│".join(f" {text:<{width}} " for text, width in zip(texts, widths, strict=True)) + "│"

    lines = [border("top"), line(titles)]
    for index, row in enumerate(cells):
        lines.append(border("header" if index == 0 else "row"))
        height = max([1, *(len(cell) for cell in row)])
        lines += [line([cell[at] if at < len(cell) else "" for cell in row]) for at in range(height)]
    return [*lines, border("bottom")]


def _key_value(data: dict) -> list[str]:
    """An object as pveum's text format prints one (PVE::CLIFormatter's print_api_result): a row for each key that has
    a value, by key, a string as it is and any other value as compact JSON, an object's keys sorted."""

    def text(value: object) -> str:
        return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), sort_keys=True)

    return _table(["key", "value"], [[key, text(data[key])] for key in sorted(data) if data[key] is not None])


def _permissions(data: dict) -> list[str]:
    """A user's permissions as pveum's text format prints them (pveum.pm's print_perm_result): a row for each path, its
    privileges a line each, by name, (*) after one that propagates, and a line that says so."""
    rows = [
        [path, "\n".join(f"{name} (*)" if propagate else name for name, propagate in sorted(held.items()))]
        for path, held in sorted(data.items())
    ]
    return [*_table(["ACL path", "Permissions"], rows), "Permissions marked with '(*)' have the 'propagate' flag set."]


def _printed(options: dict, data: dict, table=_key_value) -> str:
    """What pveum prints of an answer: compact JSON under --output-format json, else its text format's table."""
    return _json(data) if options.get("output-format") == "json" else "\n".join(table(data))


def _pvesh(state: dict, words: list[str]) -> tuple[int, object]:
    (verb, path, *_), options = _options(words)
    if verb != "get" or options.get("output-format") != "json":
        return _refuse(f"fake pvesh: unsupported call {words}"), None
    refused = _unparsed(options)
    if refused is not None:
        return refused, None
    parts = path.strip("/").split("/")

    def takes(*names: str) -> int | None:
        """The refusal of an option this read does not take here; None where it takes them all."""
        return _unmodelled(options, {"output-format", *names}, "pvesh", words)

    if parts[:2] == ["access", "roles"] and len(parts) == 3:
        if (refused := takes()) is not None:
            return refused, None
        if parts[2] not in state["roles"]:
            return _refuse(f"role '{parts[2]}' does not exist"), None
        return 0, dict.fromkeys(state["roles"][parts[2]], 1)
    if parts[:2] == ["access", "users"] and len(parts) == 3:
        if (refused := takes()) is not None:
            return refused, None
        errors = _ids_refused({}, parts[2])
        if errors:
            return _refuse_parameters(errors), None
        if parts[2] not in state["users"]:
            return _refuse(f"no such user ('{parts[2]}')"), None
        # As read_user answers: the user's comment where it has one, and an email, empty, where user.cfg's line holds a
        # field after it, as a comment is (parse_user_config keeps the email split gives it); its groups (none here)
        # and its tokens by name, each as read back, or null where it has none (PVE/API2/User.pm, $extract_user_data).
        tokens = {
            full_id.split("!", 1)[1]: _token_info(token)
            for full_id, token in sorted(state["tokens"].items())
            if full_id.split("!", 1)[0] == parts[2]
        }
        user = state["users"][parts[2]]
        email = {"email": ""} if user.get("comment") else {}
        return 0, {"enable": 1, "expire": 0, **email, **_comment(user), "groups": [], "tokens": tokens or None}
    if parts == ["cluster", "resources"] and options.get("type") == "vm":
        if (refused := takes("type")) is not None:
            return refused, None
        return 0, [
            {"id": f"qemu/{g['vmid']}", "type": "qemu", "node": state["node"], "status": "stopped", **g}
            for g in state.get("guests", [])
        ]
    if parts == ["pools"]:
        # As Pool.pm's index answers: every pool with its comment; with --poolid, that pool with its members too.
        if (refused := takes("poolid")) is not None:
            return refused, None
        pools = state.get("pools", {})
        if "poolid" not in options:
            return 0, [_pool(name, pool) for name, pool in sorted(pools.items())]
        if options["poolid"] not in pools:
            return _refuse(f"pool '{options['poolid']}' does not exist"), None
        return 0, [_pool(options["poolid"], pools[options["poolid"]]) | {"members": _members(state, options["poolid"])}]
    if parts[0] == "pools" and len(parts) == 2:
        # The deprecated read of one pool, which Proxmox VE 9 still answers as the listing with its poolid, unlisted.
        if (refused := takes()) is not None:
            return refused, None
        if parts[1] not in state.get("pools", {}):
            return _refuse(f"pool '{parts[1]}' does not exist"), None
        return 0, _pool(parts[1], state["pools"][parts[1]]) | {"members": _members(state, parts[1])}
    if parts == ["access", "acl"]:
        # As Proxmox lists them: each ACL's path, its user's or group's name, the role and whether it propagates.
        if (refused := takes()) is not None:
            return refused, None
        keys = ("path", "ugid", "roleid", "propagate", "type")
        return 0, [{key: acl[key] for key in keys if key in acl} for acl in state["acls"]]
    if parts[:2] == ["access", "users"] and len(parts) == 5 and parts[3] == "token":
        if (refused := takes()) is not None:
            return refused, None
        errors = _ids_refused({}, parts[2], parts[4])
        if errors:
            return _refuse_parameters(errors), None
        token, refused = _token(state, parts[2], parts[4])
        return (refused, None) if token is None else (0, _token_info(token))
    if parts[0] == "nodes" and len(parts) == 3:
        if parts[1] != state["node"]:
            return _refuse(f"hostname lookup '{parts[1]}' failed - failed to get address info"), None
        if parts[2] == "storage" and options.get("content") == "images":
            if (refused := takes("content", "enabled")) is not None:
                return refused, None
            listed = [s for s in state["storages"] if "images" in s["content"].split(",")]
            return 0, [{"active": 1, "enabled": 1, "shared": 0, **s} for s in listed]
        if parts[2] == "network" and options.get("type") == "any_local_bridge":
            if (refused := takes("type")) is not None:
                return refused, None
            return 0, [{"active": 1, "iface": b, "type": "bridge"} for b in state["bridges"]]
    return _refuse(f"fake pvesh: no such path {path}"), None


def _pveum(state: dict, words: list[str]) -> tuple[int, str | None]:
    positional, options = _options(words)
    command = next((command for command in PVEUM_OPTIONS if tuple(positional[: len(command)]) == command), None)
    if command is None:
        return _refuse(f"fake pveum: unsupported call {words}"), None
    if set(options) - PVEUM_OPTIONS[command] or options.get("output-format") in ("json-pretty", "yaml"):
        return _refuse(f"fake pveum: unsupported call {words}"), None
    refused = _unparsed(options)
    if refused is not None:
        return refused, None
    errors = _parameters_refused(command, positional[len(command) :], options, state)
    if errors:
        return _refuse_parameters(errors), None
    match positional:
        case ["role", "add", role]:
            if role.upper().startswith("PVE"):
                namespace = "cannot use role ID starting with the (case-insensitive) 'PVE' namespace"
                return _refuse_parameters({"roleid": namespace}), None
            if role in state["roles"] or role in SPECIAL_ROLES:
                return _refuse(f"create role failed: role '{role}' already exists"), None
            state["roles"][role] = _split(options.get("privs"))
            return 0, None
        case ["role", "modify", role]:
            # As pveum: --append 1 adds the privileges given to what the role holds; without it, they replace it.
            if role not in state["roles"]:
                return _refuse(f"update role failed: role '{role}' does not exist"), None
            privileges = _split(options.get("privs"))
            kept = state["roles"][role] if _boolean(options, "append", 0) else []
            state["roles"][role] = [*kept, *(p for p in privileges if p not in kept)]
            return 0, None
        case ["pool", "add", name]:
            if name in state.setdefault("pools", {}):
                return _refuse(f"create pool failed: pool '{name}' already exists"), None
            if not re.match(r"[A-Za-z]", name):
                return _refuse("create pool failed: pool name must start with a letter"), None
            state["pools"][name] = _comment(options)
            return 0, None
        case ["pool", "modify", name]:
            return _pool_modify(state, name, options, words), None
        case ["acl", "delete", path]:
            role, user = options["roles"], options["users"]
            before = len(state["acls"])
            state["acls"] = [a for a in state["acls"] if (a["path"], a["ugid"], a["roleid"]) != (path, user, role)]
            return (0, None) if len(state["acls"]) < before else (_refuse("no such ACL"), None)
        case ["user", "add", user]:
            if user in state["users"]:
                return _refuse(f"create user failed: user '{user}' already exists"), None
            state["users"][user] = _comment(options)
            return 0, None
        case ["acl", "modify", path]:
            # As update_acl: each role given each user and each token named, at the path. The state keeps no groups.
            return _acl_modify(state, path, options), None
        case ["user", "permissions", user] if "!" not in user and "path" in options:
            # As permissions answers for a user, whether it exists or not: the privileges it holds on the path. A
            # token's own, or every path's, is not modelled.
            path = options["path"]
            return 0, _printed(options, {path: _held(state, user, path)}, _permissions)
        case ["user", "token", "add", user, name]:
            if user not in state["users"]:
                return _refuse(f"no such user ('{user}')"), None
            if f"{user}!{name}" in state["tokens"]:
                return _refuse_parameters({"tokenid": "Token already exists."}), None
            privsep = _boolean(options, "privsep", 1)
            secret = state.pop("next_secret", None) or str(uuid.uuid4())
            state["tokens"][f"{user}!{name}"] = {"privsep": privsep, "secret": secret, **_comment(options)}
            info = {"privsep": str(privsep), **_comment(options)}
            return 0, _printed(options, {"full-tokenid": f"{user}!{name}", "info": info, "value": secret})
        case ["user", "token", "modify", user, name]:
            # As update_token_info with --regenerate: the token as kept, with its new secret and its full ID.
            token, refused = _token(state, user, name)
            if token is None:
                return refused, None
            answer = _token_info(token)
            if _boolean(options, "regenerate", 0):
                token["secret"] = state.pop("next_secret", None) or str(uuid.uuid4())
                answer |= {"full-tokenid": f"{user}!{name}", "value": token["secret"]}
            return 0, _printed(options, answer)
        case ["user", "token", "delete" | "remove", user, name]:
            token, refused = _token(state, user, name)
            if token is None:
                return refused, None
            del state["tokens"][f"{user}!{name}"]
            # Its ACL entries go with it: Proxmox drops an entry that names no token when it reads user.cfg again.
            state["acls"] = [a for a in state["acls"] if (a.get("type"), a["ugid"]) != ("token", f"{user}!{name}")]
            return 0, None
    return _refuse(f"fake pveum: unsupported call {words}"), None


def _pool_modify(state: dict, name: str, options: dict, words: list[str]) -> int:
    """pveum pool modify, as update_pool under its lock: --comment replaces the pool's comment, an empty one leaving it
    none; --vms adds guests to it, refusing one that is its member already, and one of another pool without
    --allow-move. Proxmox takes a VMID that is no guest's into the pool's list, which the state, keeping a guest's pool
    on the guest, cannot hold: such a call is refused as unsupported."""
    if name not in state.setdefault("pools", {}):
        return _refuse(f"update pools failed: pool '{name}' does not exist")
    move = _boolean(options, "allow-move", 0)
    for vmid in [int(v) for v in (options.get("vms") or "").split(",") if v]:
        guest = next((g for g in state.get("guests", []) if g["vmid"] == vmid), None)
        if guest is None:
            return _refuse(f"fake pveum: unsupported call {words}")
        if guest.get("pool") == name:
            return _refuse(f"update pools failed: VM {vmid} is already a pool member")
        if guest.get("pool") and not move:
            refused = f"VM {vmid} belongs already to pool '{guest['pool']}' and 'allow-move' is not set"
            return _refuse(f"update pools failed: {refused}")
        guest["pool"] = name
    if "comment" in options:
        state["pools"][name] = {key: value for key, value in state["pools"][name].items() if key != "comment"}
        state["pools"][name] |= _comment(options)
    return 0


def _acl_modify(state: dict, path: str, options: dict) -> int:
    """pveum acl modify, as pve-access-control's update_acl, once the schema took its parameters: a user or a token
    required, then the path, normalised and known; then, under its lock, each role, and for each its users and its
    tokens, checked before anything is written; then each role given to each user and each token at the path, an entry
    there of the same role replaced."""
    roles, users, tokens = (_split(options.get(kind)) for kind in ("roles", "users", "tokens"))
    if not users and not tokens:
        either = "either 'users', 'groups' or 'tokens' is required."
        return _refuse_parameters(dict.fromkeys(("groups", "tokens", "users"), either))
    at = _acl_path(path)
    if at is None:
        return _refuse_parameters({"path": f"invalid ACL path '{path}'"})
    for role in roles:
        if role not in state["roles"]:
            return _refuse(f"ACL update failed: role '{role}' does not exist")
        for user in users:
            if user not in state["users"]:
                return _refuse(f"ACL update failed: user '{user}' does not exist")
        for full_id in tokens:
            user, _, name = full_id.partition("!")
            token, refused = _token(state, user, name, "ACL update failed: ")
            if token is None:
                return refused
    propagate = _boolean(options, "propagate", 1)
    for role in roles:
        for kind, who in [("user", user) for user in users] + [("token", full_id) for full_id in tokens]:
            state["acls"] = [a for a in state["acls"] if (a["path"], a["ugid"], a["roleid"]) != (at, who, role)]
            state["acls"].append({"path": at, "ugid": who, "roleid": role, "propagate": propagate, "type": kind})
    return 0


@contextmanager
def _locked(state_path: Path):
    """The host's state held by this call alone, from its read to its write: a lock on a file beside it, as Proxmox
    holds user.cfg under its cluster's lock, so two calls at once never lose a write or meet half a state."""
    with state_path.with_name(state_path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _write(state_path: Path, state: dict) -> None:
    """The state written whole, by a rename over the one before."""
    written = state_path.with_name(f".{state_path.name}.{os.getpid()}")
    written.write_text(json.dumps(state))
    os.replace(written, state_path)


def main(argv: list[str]) -> int:
    state_file, command, *words = argv
    state_path = Path(state_file)
    with _locked(state_path):
        kept = state_path.read_text()
        state = json.loads(kept)
        with (state_path.parent / "calls.log").open("a") as log:
            log.write(json.dumps([command, *words]) + "\n")
        if state.get("fail") and state["fail"] in " ".join([command, *words]):
            print(f"fake {command}: refused, as the test asked", file=sys.stderr)
            return 1
        if command == "pveversion":
            print(f"pve-manager/{state['version']}/fakefakefakefake (running kernel: 6.8.12-1-pve)")
            return 0
        if command == "hostname":
            print(state["node"])
            return 0
        if command == "id":
            print(state["uid"] if words == ["-u"] else f"uid={state['uid']}")
            return 0
        if command == "pvesh":
            rc, data = _pvesh(state, words)
            output = None if data is None else _json(data)
        else:
            rc, output = _pveum(state, words)
        if rc == 0:
            if state != json.loads(kept):
                _write(state_path, state)
            if output is not None:
                sys.stdout.buffer.write(f"{output}\n".encode())
        return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
