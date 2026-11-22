"""Stateful stand-in for the Proxmox VE commands tools/proxmox-access.sh runs: pveum, pvesh, pveversion, hostname, id.

Run as `fake_pve_cli.py <state.json> <command> <args...>`, through a wrapper of
each command's name (install() writes them). It keeps the host in one JSON file
- the version, the node, its storages and bridges, the roles, users, ACLs,
tokens and pools - answers only the calls the script makes, as Proxmox answers them
(compact JSON, `pveum` refusing what exists), and appends every call to
calls.log beside the state. A call whose words contain the state's "fail"
string fails, as a refused write would.

It answers too what the new engine's init will run there, as Proxmox VE 9 does
(pve-access-control's PVE/API2/User.pm and ACL.pm, pve-manager's
PVE/API2/Pool.pm): a pool made with a comment, given one with `pveum pool
modify --comment`, and read with it by `pvesh get /pools` (with --poolid, or
the deprecated /pools/<pool>, its members too); an ACL entry for a token
(`pveum acl modify <path> --tokens <user@realm!name>`); a token made with
--privsep 1 and its secret made again (`pveum user token modify --regenerate`);
and the reads that say who made an object: a role's privileges, a user's and
a token's comment, a token's privsep. The state keeps what Proxmox keeps in
user.cfg, which it reads again at every call: a comment only where it is not
empty, and no ACL entry of a token that is gone. A boolean is read as
Proxmox's command line reads one (given bare, 1). An option a pveum command
does not take here (PVEUM_OPTIONS), or a read of a user, a token or the pools,
is refused as an unsupported call, never ignored.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import uuid
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
# ignored, which would answer as though Proxmox had done what it asked.
PVEUM_OPTIONS = {
    ("role", "add"): {"privs"},
    ("role", "modify"): {"privs", "append"},
    ("pool", "add"): {"comment"},
    ("pool", "modify"): {"vms", "allow-move", "comment"},
    ("acl", "modify"): {"roles", "users", "tokens", "propagate"},
    ("acl", "delete"): {"roles", "users"},
    ("user", "add"): {"comment"},
    ("user", "permissions"): {"path", "output-format"},
    ("user", "token", "add"): {"privsep", "comment", "expire", "output-format"},
    ("user", "token", "modify"): {"privsep", "comment", "expire", "regenerate", "delete", "output-format"},
    ("user", "token", "delete"): set(),
    ("user", "token", "remove"): set(),
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
    """Positional words, and --name value pairs: a --name with no value after it (the last word, or another --name
    next) has the value "", as Proxmox's command line gives a boolean given bare (PVE::JSONSchema's get_options)."""
    positional, options, index = [], {}, 0
    while index < len(words):
        if words[index].startswith("--"):
            bare = index + 1 == len(words) or words[index + 1].startswith("--")
            options[words[index][2:]] = "" if bare else words[index + 1]
            index += 1 if bare else 2
        else:
            positional.append(words[index])
            index += 1
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
    """A pool's guests, as Proxmox lists a pool's members (pve-manager's PVE/API2Tools.pm, extract_vm_stats): by VMID,
    each its ID, VMID, node, type and status, and the name and template flag its statistics carry; never its pool or
    its tags, which only /cluster/resources names."""
    members = []
    for guest in sorted((g for g in state.get("guests", []) if g.get("pool") == name), key=lambda g: g["vmid"]):
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


def _refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _refuse_parameters(errors: dict) -> int:
    """A parameter Proxmox refuses, as its command line says one: the 400, then each parameter's words."""
    return _refuse("\n".join(["400 Parameter verification failed.", *(f"{k}: {v}" for k, v in sorted(errors.items()))]))


def _unmodelled(options: dict, known: set, command: str, words: list[str]) -> int | None:
    """The refusal of an option the call does not take here, as an unsupported call; None where it takes them all."""
    return _refuse(f"fake {command}: unsupported call {words}") if set(options) - known else None


def _pvesh(state: dict, words: list[str]) -> tuple[int, object]:
    (verb, path, *_), options = _options(words)
    if verb != "get" or options.get("output-format") != "json":
        return _refuse(f"fake pvesh: unsupported call {words}"), None
    parts = path.strip("/").split("/")
    if parts[:2] == ["access", "roles"] and len(parts) == 3:
        if parts[2] not in state["roles"]:
            return _refuse(f"role '{parts[2]}' does not exist"), None
        return 0, dict.fromkeys(state["roles"][parts[2]], 1)
    if parts[:2] == ["access", "users"] and len(parts) == 3:
        refused = _unmodelled(options, {"output-format"}, "pvesh", words)
        if refused is not None:
            return refused, None
        if parts[2] not in state["users"]:
            return _refuse(f"no such user ('{parts[2]}')"), None
        # As read_user answers: the user's comment where it has one, its groups (none here) and its tokens by name,
        # each as read back, or null where it has none (PVE/API2/User.pm, $extract_user_data).
        tokens = {
            full_id.split("!", 1)[1]: _token_info(token)
            for full_id, token in sorted(state["tokens"].items())
            if full_id.split("!", 1)[0] == parts[2]
        }
        user = state["users"][parts[2]]
        return 0, {"enable": 1, "expire": 0, **_comment(user), "groups": [], "tokens": tokens or None}
    if parts == ["cluster", "resources"] and options.get("type") == "vm":
        return 0, [
            {"id": f"qemu/{g['vmid']}", "type": "qemu", "node": state["node"], "status": "stopped", **g}
            for g in state.get("guests", [])
        ]
    if parts == ["pools"]:
        # As Pool.pm's index answers: every pool with its comment; with --poolid, that pool with its members too.
        refused = _unmodelled(options, {"output-format", "poolid"}, "pvesh", words)
        if refused is not None:
            return refused, None
        pools = state.get("pools", {})
        if "poolid" not in options:
            return 0, [_pool(name, pool) for name, pool in sorted(pools.items())]
        if options["poolid"] not in pools:
            return _refuse(f"pool '{options['poolid']}' does not exist"), None
        return 0, [_pool(options["poolid"], pools[options["poolid"]]) | {"members": _members(state, options["poolid"])}]
    if parts[0] == "pools" and len(parts) == 2:
        # The deprecated read of one pool, which Proxmox VE 9 still answers as the listing with its poolid, unlisted.
        refused = _unmodelled(options, {"output-format"}, "pvesh", words)
        if refused is not None:
            return refused, None
        if parts[1] not in state.get("pools", {}):
            return _refuse(f"pool '{parts[1]}' does not exist"), None
        return 0, _pool(parts[1], state["pools"][parts[1]]) | {"members": _members(state, parts[1])}
    if parts == ["access", "acl"]:
        # As Proxmox lists them: each ACL's path, its user's or group's name, the role and whether it propagates.
        keys = ("path", "ugid", "roleid", "propagate", "type")
        return 0, [{key: acl[key] for key in keys if key in acl} for acl in state["acls"]]
    if parts[:2] == ["access", "users"] and len(parts) == 5 and parts[3] == "token":
        refused = _unmodelled(options, {"output-format"}, "pvesh", words)
        if refused is not None:
            return refused, None
        token = state["tokens"].get(f"{parts[2]}!{parts[4]}")
        if token is None:
            return _refuse(f"no such token '{parts[4]}' for user '{parts[2]}'"), None
        return 0, _token_info(token)
    if parts[0] == "nodes" and len(parts) == 3:
        if parts[1] != state["node"]:
            return _refuse(f"hostname lookup '{parts[1]}' failed - failed to get address info"), None
        if parts[2] == "storage" and options.get("content") == "images":
            listed = [s for s in state["storages"] if "images" in s["content"].split(",")]
            return 0, [{"active": 1, "enabled": 1, "shared": 0, **s} for s in listed]
        if parts[2] == "network" and options.get("type") == "any_local_bridge":
            return 0, [{"active": 1, "iface": b, "type": "bridge"} for b in state["bridges"]]
    return _refuse(f"fake pvesh: no such path {path}"), None


def _pveum(state: dict, words: list[str]) -> tuple[int, object]:
    positional, options = _options(words)
    known = next(
        (given for command, given in PVEUM_OPTIONS.items() if tuple(positional[: len(command)]) == command), None
    )
    refused = None if known is None else _unmodelled(options, known, "pveum", words)
    if refused is not None:
        return refused, None
    match positional:
        case ["role", "add", role]:
            if role in state["roles"]:
                return _refuse(f"role '{role}' already exists"), None
            privileges = options.get("privs", "").replace(";", ",").replace(" ", ",").split(",")
            unknown = [p for p in privileges if p and p not in _known(state)]
            if unknown:
                return _refuse(f"invalid privilege '{unknown[0]}'"), None
            state["roles"][role] = [p for p in privileges if p]
            return 0, None
        case ["role", "modify", role]:
            # As pveum: --append 1 adds the privileges given to what the role holds; without it, they replace it.
            if role not in state["roles"]:
                return _refuse(f"role '{role}' does not exist"), None
            privileges = [p for p in options.get("privs", "").replace(";", ",").replace(" ", ",").split(",") if p]
            unknown = [p for p in privileges if p not in _known(state)]
            if unknown:
                return _refuse(f"invalid privilege '{unknown[0]}'"), None
            append = _boolean(options, "append", 0)
            if append is None:
                return _refuse("400 unable to parse boolean option"), None
            kept = state["roles"][role] if append else []
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
            # As pveum: --vms adds guests to the pool, refusing one that is in another without --allow-move; --comment
            # replaces the pool's comment, an empty one leaving it none.
            if name not in state.setdefault("pools", {}):
                return _refuse(f"pool '{name}' does not exist"), None
            move = _boolean(options, "allow-move", 0)
            if move is None:
                return _refuse("400 unable to parse boolean option"), None
            for vmid in [int(v) for v in options.get("vms", "").split(",") if v]:
                guest = next((g for g in state.get("guests", []) if g["vmid"] == vmid), None)
                if guest is None:
                    return _refuse(f"no such VMID '{vmid}'"), None
                if guest.get("pool") and guest["pool"] != name and not move:
                    return _refuse(f"VM {vmid} belongs already to pool '{guest['pool']}'"), None
                guest["pool"] = name
            if "comment" in options:
                state["pools"][name] = {key: value for key, value in state["pools"][name].items() if key != "comment"}
                state["pools"][name] |= _comment(options)
            return 0, None
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
        case ["user", "permissions", user]:
            if user not in state["users"]:
                return _refuse(f"user '{user}' does not exist"), None
            path = options["path"]
            return 0, {path: _held(state, user, path)}
        case ["user", "token", "add", user, name]:
            if user not in state["users"]:
                return _refuse(f"no such user ('{user}')"), None
            if f"{user}!{name}" in state["tokens"]:
                return _refuse("Token already exists."), None
            privsep = _boolean(options, "privsep", 1)
            if privsep is None:
                return _refuse("400 unable to parse boolean option"), None
            if not re.fullmatch(r"[0-9]+", options.get("expire", "0")):
                return _refuse_parameters(
                    {"expire": f"type check ('integer') failed - got '{options['expire']}'"}
                ), None
            expire = {"expire": int(options["expire"])} if "expire" in options else {}
            secret = state.pop("next_secret", None) or str(uuid.uuid4())
            state["tokens"][f"{user}!{name}"] = {"privsep": privsep, "secret": secret, **_comment(options), **expire}
            info = {"privsep": str(privsep), **_comment(options), **expire}
            return 0, {"full-tokenid": f"{user}!{name}", "info": info, "value": secret}
        case ["user", "token", "modify", user, name]:
            return _token_modify(state, user, name, options)
        case ["user", "token", "delete" | "remove", user, name]:
            if state["tokens"].pop(f"{user}!{name}", None) is None:
                return _refuse(f"no such token '{name}' for user '{user}'"), None
            # Its ACL entries go with it: Proxmox drops an entry that names no token when it reads user.cfg again.
            state["acls"] = [a for a in state["acls"] if (a.get("type"), a["ugid"]) != ("token", f"{user}!{name}")]
            return 0, None
    return _refuse(f"fake pveum: unsupported call {words}"), None


def _acl_modify(state: dict, path: str, options: dict) -> int:
    """pveum acl modify, as pve-access-control's update_acl: every role, user and token checked before anything is
    written, then each role given to each user and each token at path, an entry there of the same role replaced."""
    roles, users, tokens = (_split(options.get(kind)) for kind in ("roles", "users", "tokens"))
    if not users and not tokens:
        either = "either 'users', 'groups' or 'tokens' is required."
        return _refuse_parameters(dict.fromkeys(("groups", "tokens", "users"), either))
    if not roles:
        return _refuse_parameters({"roles": "property is missing and it is not optional"})
    for role in roles:
        if role not in state["roles"]:
            return _refuse(f"role '{role}' does not exist")
    for user in users:
        if user not in state["users"]:
            return _refuse(f"user '{user}' does not exist")
    for full_id in tokens:
        user, bang, name = full_id.partition("!")
        if not (bang and name and "@" in user):
            return _refuse(f"'{full_id}' is not a valid token ID - not able to split into user and token parts")
        if full_id not in state["tokens"]:
            return _refuse(f"no such token '{name}' for user '{user}'")
    propagate = _boolean(options, "propagate", 1)
    if propagate is None:
        return _refuse("400 unable to parse boolean option")
    for role in roles:
        for kind, who in [("user", user) for user in users] + [("token", full_id) for full_id in tokens]:
            state["acls"] = [a for a in state["acls"] if (a["path"], a["ugid"], a["roleid"]) != (path, who, role)]
            state["acls"].append({"path": path, "ugid": who, "roleid": role, "propagate": propagate, "type": kind})
    return 0


def _token_modify(state: dict, user: str, name: str, options: dict) -> tuple[int, object]:
    """pveum user token modify, as update_token_info: its privsep, expire and comment changed, the comment deleted
    (--delete comment, the one setting it deletes), and with --regenerate a new secret; it answers the token as kept,
    with the new secret and its full ID when one was made."""
    if user not in state["users"]:
        return _refuse(f"no such user ('{user}')"), None
    token = state["tokens"].get(f"{user}!{name}")
    if token is None:
        return _refuse(f"no such token '{name}' for user '{user}'"), None
    deleted = _split(options.get("delete"))
    for key in deleted:
        if key != "comment":
            return _refuse_parameters({"delete": f"unknown option '{key}'"}), None
    privsep, regenerate = _boolean(options, "privsep", token["privsep"]), _boolean(options, "regenerate", 0)
    if privsep is None or regenerate is None:
        return _refuse("400 unable to parse boolean option"), None
    if not re.fullmatch(r"[0-9]+", options.get("expire", "0")):
        return _refuse_parameters({"expire": f"type check ('integer') failed - got '{options['expire']}'"}), None
    token["privsep"] = privsep
    if "expire" in options:
        token["expire"] = int(options["expire"])
    if deleted or "comment" in options:
        token.pop("comment", None)
        token |= _comment(options)
    answer = _token_info(token)
    if regenerate:
        token["secret"] = state.pop("next_secret", None) or str(uuid.uuid4())
        answer |= {"full-tokenid": f"{user}!{name}", "value": token["secret"]}
    return 0, answer


def main(argv: list[str]) -> int:
    state_file, command, *words = argv
    state_path = Path(state_file)
    state = json.loads(state_path.read_text())
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
    rc, output = (_pvesh if command == "pvesh" else _pveum)(state, words)
    if rc == 0:
        state_path.write_text(json.dumps(state))
        if output is not None:
            print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
