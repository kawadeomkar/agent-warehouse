"""What a VM's reads mean: the probe's answers (playbooks/module_utils/status_probe.py) made the parts just status's
model keeps, with the words the roles themselves say (claude_vm.status.lines).

The state is worked out here as the roles' vars/main.yml work it out from their own reads, for ansible-core's check
mode, which is what just status is to them: tests/unit/test_status_vms.py runs the roles' own tasks and these on the
same stand-in VMs, and holds the two equal. Whatever a VM sent is data, read with care for its shape: its root
chooses every byte.

Nothing here reads or writes anything.
"""

from __future__ import annotations

import json
import os
import re

from ..proxmox import tailnet_ipv4
from ..text import plain_text
from . import model
from .lines import github_login_report, tailscale_report

UNIT_KEYS = ("LoadState", "ActiveState", "SubState", "NRestarts", "Result", "StateChangeTimestamp")
PATH_KEYS = ("LoadState", "ActiveState", "SubState")
VERSION = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+")


def _map(value):
    return value if isinstance(value, dict) else {}


def _parsed(text):
    """The JSON object a tool printed, or {}."""
    try:
        said = json.loads(text or "")
    except (RecursionError, TypeError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


def parts_wanted(sections):
    """Which of a VM's reads these sections ask for, by the name each has in the probe's answer."""
    wanted = set()
    if "system" in sections or "work" in sections:
        wanted.add("system")
    if "sign-ins" in sections:
        wanted |= {"claude", "github"}
    if "work" in sections:
        wanted.add("work")
    if "sign-ins" in sections or "access" in sections:
        wanted.add("tailscale")
    return wanted


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def system_found(part):
    """What the probe read of the system, the model's keys alone, each of the shape the report draws: a VM's root
    chooses what is sent."""
    system = model.status_system_read(part)
    if system is None:
        return None
    load = system.get("load")
    numbers = isinstance(load, list) and len(load) == 3 and all(isinstance(one, (int, float)) for one in load)
    system["load"] = load if numbers else []
    system["memory"] = {key: value for key, value in _map(system.get("memory")).items() if _count(value) is not None}
    for key in ("upgrades", "security"):
        system[key] = _count(system.get(key))
    for key in ("claude", "uptime", "vscode_windows", "editor_windows"):
        system[key] = _count(system.get(key)) or 0
    system["editor_windows_by"] = {
        k: v for k, v in _map(system.get("editor_windows_by")).items() if _count(v) is not None
    }
    packages = system.get("reboot_packages")
    system["reboot_packages"] = [str(one) for one in packages] if isinstance(packages, list) else []
    system["unread"] = _map(system.get("unread"))
    return system


def claude_found(part, settings):
    target = os.path.basename(str(part.get("target") or ""))
    return {
        "installed": bool(part.get("installed")),
        "method": settings["method"],
        "version": target if VERSION.match(target) else "",
        "login_stored": bool(part.get("login")),
        "api_key": settings["api_key"],
        "remote_control_wanted": settings["remote_control_wanted"],
        "service": {key: value for key, value in _map(part.get("service")).items() if key in UNIT_KEYS},
        "path_unit": {key: value for key, value in _map(part.get("path")).items() if key in PATH_KEYS},
        "login_command": settings["login_command"],
        "unit": settings["unit"],
    }


def github_found(part, settings, host):
    found = bool(part.get("found"))
    status = _map(part.get("status"))
    rc = status.get("rc", 0)
    stdout = str(status.get("stdout") or "")
    hosts = _parsed(stdout).get("hosts", {}) if rc == 0 and stdout.strip().startswith("{") else {}
    accounts = _map(hosts).get("github.com") or []
    account = next((one for one in accounts if isinstance(one, dict) and one.get("active")), {})
    state = account.get("state", "")
    signed_in = state == "success"
    error = str(account.get("error") or "")
    unchecked = state == "error" and not re.match(r"HTTP 40[13]", error)
    if not signed_in:
        kind = "none"
    elif re.match(r".*_TOKEN$", str(account.get("tokenSource") or "")):
        kind = "env"
    else:
        kind = part.get("kind") if part.get("kind") in ("pat", "oauth") else "other"
    is_ours = signed_in and bool(part.get("marked"))
    report = github_login_report(
        {
            "gh_found": found,
            "status_rc": rc,
            "status_said": plain_text(status.get("stderr") or "").strip(),
            "signed_in": signed_in,
            "login_done": False,
            "account": account.get("login") or "",
            "kind": kind,
            "is_ours": is_ours,
            "lacks_workflow": kind == "oauth" and "workflow" not in str(account.get("scopes") or ""),
            "user": settings["user"],
            "host": host,
            "auth": settings["auth"],
            "auth_method": settings["auth_method"],
            "token_wanted": settings["token_wanted"],
            "token_set": settings["token_set"],
            "role_follows": False,
            "configure_command": settings["configure_command"],
            "login_command": settings["login_command"],
        }
    )
    return {
        "installed": found,
        "readable": found and rc == 0,
        "signed_in": signed_in,
        "unchecked": unchecked,
        "error": error if unchecked else "",
        "account": str(account.get("login") or "") if signed_in else "",
        "kind": kind,
        "managed": settings["auth"],
        "line": report["line"],
        "left": report["left"] and not unchecked,
    }


def _set_flags(prefs, settings):
    """What tailscale set would change, as settings.yml holds the node to its settings."""
    flags = []
    if prefs.get("Hostname", "") != settings["hostname"]:
        flags.append(f"--hostname={settings['hostname']}")
    if prefs.get("RunSSH", False) != settings["ssh"]:
        flags += [f"--ssh={str(settings['ssh']).lower()}", "--accept-risk=lose-ssh"]
    if prefs.get("RouteAll", False) != settings["accept_routes"]:
        flags.append(f"--accept-routes={str(settings['accept_routes']).lower()}")
    if prefs.get("CorpDNS", True) != settings["accept_dns"]:
        flags.append(f"--accept-dns={str(settings['accept_dns']).lower()}")
    return flags


def tailscale_found(part, settings, name, joined_before):
    wanted = settings["wanted"]
    package = part.get("package") == "installed"
    installed = package or bool(part.get("foreign"))
    marker = bool(part.get("marker"))
    ours = marker or not installed
    unmanaged = not wanted and marker and package
    read_state = wanted or unmanaged
    status = _map(part.get("status")) if package else {}
    up = status.get("rc", 1) == 0
    state = _parsed(status.get("stdout")) if up else {}
    backend = state.get("BackendState", "NoState")
    joined = bool(state.get("HaveNodeKey"))
    me = _map(state.get("Self"))
    key = str(me.get("PublicKey") or "")
    dns = re.sub(r"[.]$", "", str(me.get("DNSName") or ""))
    addresses = me.get("TailscaleIPs")
    ipv4 = tailnet_ipv4(addresses if isinstance(addresses, list) else [])
    no_login = backend == "NeedsLogin" and not joined
    key_wanted = settings["key_wanted"]
    signed_in = joined and backend in ("Running", "NeedsMachineAuth")
    text = str(part.get("joined") or "")
    pending = text == "pending"
    login_ours = bool(key) and ((joined and text == key) or (signed_in and pending))
    prefs = _parsed(_map(part.get("prefs")).get("stdout")) if _map(part.get("prefs")).get("rc") == 0 else {}
    tags = settings["advertise_tags"]
    mine = me.get("Tags") if isinstance(me.get("Tags"), list) else []
    label = settings["hostname"].lower()
    locally = settings["key_refused_locally"]
    report = tailscale_report(
        {
            "name": name,
            "hostname": settings["hostname"],
            "check_mode": True,
            "ours": ours,
            "check_without_package": not package,
            "daemon_up": up,
            "backend": backend,
            "joined": joined,
            "auth_url": state.get("AuthURL") or "",
            "first_join": no_login and not joined_before,
            "key_wanted": key_wanted,
            "key_refused_locally": locally,
            "key_held_back": no_login and joined_before and key_wanted,
            "join_ran": False,
            "join_stderr": "",
            "refused_why": "oauth authkeys require --advertise-tags" if locally else "",
            "dns_name": dns,
            "self_name": me.get("HostName") or "",
            "tailnet": _map(state.get("CurrentTailnet")).get("Name") or "",
            "login_server": settings["login_server"],
            "ipv4": ipv4,
            "joined_now": False,
            "login_ours": login_ours,
            "name_taken": bool(re.match("^" + re.escape(label) + "-[0-9]+$", dns.split(".")[0])),
            "set_changed": False,
            "set_cmd": [],
            "from_login": False,
            "set_flags": _set_flags(prefs, settings),
            "tags_missing": [tag for tag in dict.fromkeys(tags) if tag not in mine] if tags else [],
            "settle_seconds": 0,
            "expired_elsewhere": joined and not login_ours and not pending,
            "joined_tag": settings["joined_tag"],
            "joined_before": joined_before,
            "sign_in_follows": False,
            "auth_method": settings["auth_method"],
            "admin_page": settings["admin_page"],
            "configure_command": settings["configure_command"],
            "login_command": settings["login_command"],
        }
    )
    case = report["case"] if wanted else "unmanaged" if unmanaged else "off"
    return {
        "enabled": wanted,
        "installed": installed,
        "ours": ours,
        "daemon_up": up if read_state else False,
        "backend": backend if read_state else "",
        "case": case,
        "dns_name": dns if read_state else "",
        "ipv4": ipv4 if read_state else "",
        "key_expiry": (me.get("KeyExpiry") or "") if read_state else "",
        "line": report["line"] if wanted else "",
        "left": report["left"] if wanted else False,
    }


def _part(side, key, make, unread):
    """``make`` of the probe's answer for ``key``, or None with why it was not read in ``unread``."""
    part = side.get(key)
    if not isinstance(part, dict):
        unread[key] = str(side.get("msg") or "it said nothing")
        return None
    if part.get("failed"):
        unread[key] = str(part.get("msg") or "it failed")
        return None
    try:
        return make(part)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        unread[key] = f"its answer could not be read: {error}"
        return None


def ssh_found(read, settings, name, host, sections, joined_before):
    """A VM's "ssh" part of what the report keeps, from what its probe found."""
    unread = dict.fromkeys(("system", "claude", "github", "tailscale", "work"), "")
    if read.get("unreachable"):
        said = str(read.get("msg") or "")
        return {
            "reached": False,
            "unreachable": said,
            "refusal": model.status_refusal(said),
            "system": None,
            "claude": None,
            "github": None,
            "tailscale": None,
            "vpn": None,
            "work": None,
            "unread": unread,
        }
    wanted = parts_wanted(sections)
    failed = str(read.get("msg") or "it failed") if read.get("failed") else ""
    user, root = _map(read.get("user")), _map(read.get("root"))
    if failed:
        user, root = {"msg": failed}, {"msg": failed}
    system = _part(user, "system", system_found, unread) if "system" in wanted else None
    claude = (
        _part(user, "claude", lambda part: claude_found(part, settings["claude"]), unread)
        if "claude" in wanted
        else None
    )
    github = (
        _part(user, "github", lambda part: github_found(part, settings["github"], host), unread)
        if "github" in wanted
        else None
    )
    work = (
        _part(user, "work", lambda part: model.status_work_read(part, settings["projects_dir"] or "~/projects"), unread)
        if "work" in wanted
        else None
    )
    tailscale = (
        _part(root, "tailscale", lambda part: tailscale_found(part, settings["tailscale"], name, joined_before), unread)
        if "tailscale" in wanted
        else None
    )
    vpn = None
    if settings["vpn"] == "tailscale" and tailscale is not None:
        vpn = {
            "kind": "tailscale",
            "address": tailscale["ipv4"],
            "case": tailscale["case"],
            "line": tailscale["line"],
            "left": tailscale["left"],
        }
    return {
        "reached": True,
        "unreachable": "",
        "refusal": "",
        "system": system,
        "claude": claude,
        "github": github,
        "tailscale": tailscale,
        "vpn": vpn,
        "work": work,
        "unread": unread,
    }
