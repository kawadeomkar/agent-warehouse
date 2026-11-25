"""The access script init runs on a Proxmox host as root (lib/claude_vm/engine/access.py's as_script, around
lib/claude_vm/pve/script.py's fixed text; docs/architecture/engine.md §16.4): rendered, then run as `sh -s` under
/bin/sh, itself on standard input as root SSH hands it over, against the stand-in pveum and pvesh
(tests/molecule/fake_pve_cli.py), never a real host.

A fresh host gets every object of E1's list and the token's secret alone on standard output; a second run changes
nothing and prints no secret; a role an older version made gets what it lacks and loses nothing; and what the script
decides is what `plan` decides of the same host: its dry run prints one line for each step of
`plan(objects(host), found)`, quoted as shlex quotes them, and says each of `plan`'s sentences, over every state and
option set here. It runs only as root, on Proxmox VE 9 or later; refuses root@pam, a value of the wrong form before any
pveum or pvesh, an option it does not have (today's --move and --template among them), a pool another fleet marked or
whose mark cannot be read, and a token made without privilege separation, each leaving the host byte for byte as it
was; takes a pool with no mark only given `--pool <name> --take-unmarked`, its comment kept and the mark added at its
end. It reads every answer as JSON through Perl, so a pool's comment is read as fleet/mark.py reads it, held by a
corpus of comments. A person's own template is looked up by its name, its network cards' bridges from its
configuration, which the stand-in does not read: a pvesh of this file's own, first on PATH, answers that one read and
hands every other to the stand-in.

The rule with a test of its own (V2-9): no file of the checkout holds a rendering of the list, found by its header.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
from harness import _pve_cli, _pve_cli_calls, _pve_cli_state, _pve_cli_writes, repo_texts

from claude_vm.engine import access
from claude_vm.fleet import mark, record
from claude_vm.schema import printer

FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
OTHER = "0d6c9b1e-2f3a-4b5c-8d7e-6f5a4b3c2d1e"
CONTROLLER = "a1b2c3d4-e5f6-4a7b-9c8d-0e1f2a3b4c5d"
MARK = mark.Mark(FLEET, mark.NONE).line
SECRET = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN_ID = f"{access.USER}!{access.TOKEN}"
# A note of the person's own in a pool's comment.
NOTE = "the dev VMs, ask Sam"
TEMPLATE = 9500
# Privileges beyond the engine's, each one the stand-in knows.
WIDER = ["Datastore.Allocate", "Sys.Modify"]
# The rendered header, which names the node and the fleet it was rendered for, after init's line as the printer
# writes it, the program as it was started.
HEADER = re.compile(
    r"rendered by .{1,200}? init(?: --host [A-Za-z0-9-]+)? for node [A-Za-z0-9-]+ of fleet"
    r" [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)


def _host(**changes: object) -> access.Host:
    values: dict[str, object] = {"fleet": FLEET, "node": "pve", "storage": "local-lvm", "bridges": ("vmbr0",)}
    return access.Host(**{**values, **changes})  # type: ignore[arg-type]


def _install(tmp_path: Path, state: dict | None = None, config: dict | None = None) -> Path:
    """The stand-ins in tmp_path/pve, made with state on the first call; with config, a pvesh of this file's own in
    tmp_path/front, which answers a guest's configuration with it and hands every other call to the stand-in's."""
    host = tmp_path / "pve"
    if not (host / "pve-state.json").exists():
        _pve_cli(host, {"next_secret": SECRET} | (state or {}))
    if config is not None and not (tmp_path / "front").exists():
        front = tmp_path / "front"
        front.mkdir()
        (front / "config.json").write_text(json.dumps(config))
        pvesh = front / "pvesh"
        pvesh.write_text(
            "#!/bin/sh\n"
            'case "$1 $2 $3 $4" in "get /nodes/"*"/qemu/"*"/config --output-format json")\n'
            f"  printf '%s\\n' \"$*\" >> {shlex.quote(str(front / 'asked.log'))}\n"
            f"  cat {shlex.quote(str(front / 'config.json'))}\n  exit 0 ;;\nesac\n"
            f'exec {shlex.quote(str(host / "pvesh"))} "$@"\n'
        )
        pvesh.chmod(0o755)
    return host


def _run(
    tmp_path: Path,
    *args: str,
    state: dict | None = None,
    host: access.Host | None = None,
    custom: str = "",
    config: dict | None = None,
) -> subprocess.CompletedProcess:
    """The script rendered for host (the default one), run as `sh -s -- args` with itself on stdin, the stand-ins in
    tmp_path - made with state on the first call - first on PATH."""
    pve = _install(tmp_path, state, config)
    path = [*([str(tmp_path / "front")] if (tmp_path / "front").exists() else []), str(pve), "/usr/bin", "/bin"]
    return subprocess.run(
        ["/bin/sh", "-s", "--", *args],
        input=access.as_script(host or _host(), custom),
        env={"PATH": os.pathsep.join(path), "LC_ALL": "C"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _state(tmp_path: Path) -> dict:
    return _pve_cli_state(tmp_path / "pve")


def _state_bytes(tmp_path: Path) -> bytes:
    return (tmp_path / "pve" / "pve-state.json").read_bytes()


def _writes(tmp_path: Path) -> list[str]:
    return _pve_cli_writes(tmp_path / "pve")


def _found(state: dict) -> access.Found:
    """What the stand-in's host holds, as access.Found holds it."""
    return access.Found(
        pools={name: pool.get("comment", "") for name, pool in state.get("pools", {}).items()},
        roles=state["roles"],
        users=list(state["users"]),
        tokens={full_id: bool(token["privsep"]) for full_id, token in state["tokens"].items()},
        grants=[access.Grant(a["path"], a["roleid"], a["ugid"], bool(a["propagate"])) for a in state["acls"]],
    )


def _argv(step: access.Step) -> list[str]:
    """The command the script runs for one step of plan's."""
    obj = step.object
    match obj:
        case access.Pool():
            return ["pveum", "pool", "add", obj.name, "--comment", obj.comment]
        case access.Role() if step.append:
            return ["pveum", "role", "modify", obj.name, "--append", "1", "--privs", ",".join(step.append)]
        case access.Role():
            return ["pveum", "role", "add", obj.name, "--privs", ",".join(obj.privileges)]
        case access.User():
            return ["pveum", "user", "add", obj.name, "--comment", obj.comment]
        case access.Token():
            words = ["--privsep", "1", "--comment", obj.comment, "--output-format", "json"]
            return ["pveum", "user", "token", "add", obj.user, obj.name, *words]
        case access.Grant():
            kind = "--tokens" if "!" in obj.subject else "--users"
            return ["pveum", "acl", "modify", obj.path, kind, obj.subject, "--roles", obj.role]
    raise AssertionError(obj)


def _planned(host: access.Host, state: dict) -> access.Plan:
    return access.plan(access.objects(host), _found(state))


def _lines(planned: access.Plan) -> list[str]:
    return [shlex.join(_argv(step)) for step in planned.steps]


def _said(stderr: str) -> list[str]:
    """What the script said of a role that holds more than its own."""
    return [line for line in stderr.splitlines() if line.startswith("The role ")]


def _engine_role(*left_out: str, more: tuple[str, ...] = ()) -> list[str]:
    return [*(each for each in access.engine_privileges() if each not in left_out), *more]


def _acl(path: str, role: str, subject: str, propagate: int = 1) -> dict:
    kind = "token" if "!" in subject else "user"
    return {"path": path, "ugid": subject, "roleid": role, "propagate": propagate, "type": kind}


GUEST = {"vmid": TEMPLATE, "name": "my-template", "template": 1, "pool": ""}
CONFIG = {"net0": "virtio=BC:24:11:00:00:01,bridge=vmbr1,firewall=1", "net1": "virtio=BC:24:11:00:00:02,bridge=vmbr0"}
TWO_BRIDGES = {"bridges": ["vmbr0", "vmbr1"]}

# Each host the script is held to plan over: the stand-in's state, the script's options, the Host those options and
# that state make (the template's ID and its bridges, as the script looks them up), and what proxmox_template_custom
# names. The token's ID is the engine's.
HELD = {
    "a fresh host": ({}, (), {}, ""),
    "under pool": ({}, ("--audit-scope", "pool"), {"audit_scope": "pool"}, ""),
    "another pool, user, token, storage and bridge": (
        {
            "storages": [{"storage": "fast", "type": "zfspool", "content": "images"}],
            "bridges": ["vmbr0", "vmbr1"],
        },
        ("--pool", "dev-vms", "--user", "automation@pve", "--token", "automation", "--storage", "fast"),
        {"pool": "dev-vms", "user": "automation@pve", "token": "automation", "storage": "fast"},
        "",
    ),
    "a bridge of its own": (TWO_BRIDGES, ("--bridge", "vmbr1"), {"bridges": ("vmbr1",)}, ""),
    "made by an older version": (
        {
            "pools": {access.POOL: {"comment": f"{NOTE} {MARK}"}},
            "roles": {access.ROLE: _engine_role("Sys.Audit", "VM.GuestAgent.Audit")},
            "users": {"root@pam": {}, access.USER: {"comment": "agent-warehouse"}},
            "acls": [_acl(f"/pool/{access.POOL}", access.ROLE, access.USER)],
        },
        (),
        {},
        "",
    ),
    "a grant that does not propagate": (
        {
            "users": {"root@pam": {}, access.USER: {}},
            "roles": {access.ROLE: _engine_role()},
            "acls": [_acl("/storage/local-lvm", access.ROLE, access.USER, 0)],
        },
        (),
        {},
        "",
    ),
    "the engine's role holding more": ({"roles": {access.ROLE: _engine_role(more=tuple(WIDER))}}, (), {}, ""),
    "the audit role holding more, granted already": (
        {
            "users": {"root@pam": {}, access.USER: {}},
            "roles": {access.AUDIT_ROLE: ["VM.Audit", "VM.Console"]},
            "acls": [_acl("/vms", access.AUDIT_ROLE, access.USER)],
        },
        (),
        {},
        "",
    ),
    "the audit role lacking one and holding more": ({"roles": {access.AUDIT_ROLE: ["VM.Console"]}}, (), {}, ""),
    "--no-token, the token there and nothing else": (
        {
            "users": {"root@pam": {}, access.USER: {}},
            "tokens": {TOKEN_ID: {"privsep": 1, "secret": SECRET}},
        },
        ("--no-token",),
        {},
        "",
    ),
    "a template of the person's own": (
        {**TWO_BRIDGES, "guests": [GUEST]},
        (),
        {"bridges": ("vmbr0", "vmbr1"), "custom_template": TEMPLATE},
        "my-template",
    ),
    "a template of the person's own, its clone role holding more": (
        {**TWO_BRIDGES, "guests": [GUEST], "roles": {access.CLONE_ROLE: ["VM.Audit", "VM.Clone", "VM.Console"]}},
        ("--audit-scope", "pool"),
        {"bridges": ("vmbr0", "vmbr1"), "custom_template": TEMPLATE, "audit_scope": "pool"},
        "my-template",
    ),
    "a template of the person's own in the pool": (
        {**TWO_BRIDGES, "guests": [GUEST | {"pool": access.POOL}]},
        (),
        {"bridges": ("vmbr0", "vmbr1")},
        "my-template",
    ),
}


@pytest.mark.parametrize(("state", "args", "values", "custom"), HELD.values(), ids=HELD)
def test_what_the_script_does_is_what_plan_decides_of_the_same_host(tmp_path, state, args, values, custom) -> None:
    config = CONFIG if custom else None
    _install(tmp_path, state, config)
    before = _state(tmp_path)
    host = _host(**values)
    planned = _planned(host, before)
    lines = _lines(planned)

    dry = _run(tmp_path, *args, "--dry-run", custom=custom, config=config)
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout.splitlines() == lines
    assert _said(dry.stderr) == list(planned.said)
    assert _writes(tmp_path) == []

    made = _run(tmp_path, *args, custom=custom, config=config)
    assert made.returncode == 0, made.stderr
    assert [line[2:] for line in made.stderr.splitlines() if line.startswith("+ ")] == lines
    assert _writes(tmp_path) == [" ".join(_argv(step)) for step in planned.steps]
    assert _said(made.stderr) == list(planned.said)
    token_made = any(isinstance(step.object, access.Token) for step in planned.steps)
    assert made.stdout == (SECRET + "\n" if token_made else "")
    # Made, the host is what the list says: nothing is left to make.
    assert _planned(host, _state(tmp_path)).steps == ()
    again = _run(tmp_path, *args, "--dry-run", custom=custom, config=config)
    assert (again.returncode, again.stdout) == (0, ""), again.stderr
    if custom:
        assert set((tmp_path / "front" / "asked.log").read_text().splitlines()) == {
            f"get /nodes/pve/qemu/{TEMPLATE}/config --output-format json"
        }


# --------------------------------------------------------------- a fresh host ---
def test_a_fresh_host_gets_every_object_and_the_secret_alone_on_stdout(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == SECRET + "\n"
    assert SECRET not in result.stderr
    state = _state(tmp_path)
    assert state["pools"] == {access.POOL: {"comment": MARK}}
    assert state["roles"][access.ROLE] == list(access.engine_privileges())
    assert state["roles"][access.AUDIT_ROLE] == ["VM.Audit"]
    assert state["users"][access.USER] == {"comment": access.COMMENT}
    assert state["tokens"][TOKEN_ID] == {"privsep": 1, "secret": SECRET, "comment": access.COMMENT}
    paths = [f"/pool/{access.POOL}", "/storage/local-lvm", "/sdn/zones/localnetwork/vmbr0", "/nodes/pve"]
    wanted = [*((path, access.ROLE) for path in paths), ("/vms", access.AUDIT_ROLE)]
    for subject, kind in ((access.USER, "user"), (TOKEN_ID, "token")):
        held = [(a["path"], a["roleid"]) for a in state["acls"] if a["ugid"] == subject]
        assert held == wanted
        assert {(a["type"], a["propagate"]) for a in state["acls"] if a["ugid"] == subject} == {(kind, 1)}
    # The user's grants before the token, the token's after it: an ACL entry names only a token that exists.
    writes = _writes(tmp_path)
    token = next(index for index, line in enumerate(writes) if line.startswith("pveum user token add"))
    assert all("--users" in line for line in writes if line.startswith("pveum acl") and writes.index(line) < token)
    assert all("--tokens" in line for line in writes[token + 1 :])
    assert len(writes) == 15


def test_a_second_run_changes_nothing_and_prints_no_secret(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 0
    before = _state_bytes(tmp_path)
    for args in ((), ("--dry-run",)):
        result = _run(tmp_path, *args)
        assert (result.returncode, result.stdout) == (0, ""), result.stderr
        assert "Nothing to do: the access is set up." in result.stderr
        assert f"The token '{TOKEN_ID}' is there" in result.stderr
        assert "pveum user token delete agent-warehouse@pve aw deletes it" in result.stderr
    assert _state_bytes(tmp_path) == before


@pytest.mark.parametrize("more", [(), tuple(WIDER)], ids=["lacking", "lacking, and holding more"])
def test_a_role_an_older_version_made_gets_what_it_lacks_and_loses_nothing(tmp_path: Path, more) -> None:
    older = _engine_role("Sys.Audit", "VM.Clone", more=more)
    result = _run(tmp_path, state={"roles": {access.ROLE: older}})
    assert result.returncode == 0, result.stderr
    assert _state(tmp_path)["roles"][access.ROLE] == [*older, "Sys.Audit", "VM.Clone"]
    assert "pveum role modify AgentWarehouse --append 1 --privs Sys.Audit,VM.Clone" in _writes(tmp_path)
    assert bool(_said(result.stderr)) == bool(more)


# ------------------------------------------------------------ nothing changed ---
def test_no_token_with_no_token_there_leaves_the_host_as_it_is(tmp_path: Path) -> None:
    _install(tmp_path)
    before = _state_bytes(tmp_path)
    for args in (("--no-token",), ("--no-token", "--dry-run")):
        result = _run(tmp_path, *args)
        assert (result.returncode, result.stdout) == (1, ""), result.stderr
        assert f"--no-token, and the token '{TOKEN_ID}' is not on this host" in result.stderr
    assert _state_bytes(tmp_path) == before
    assert _writes(tmp_path) == []


def test_the_dry_run_and_the_list_write_nothing(tmp_path: Path) -> None:
    dry = _run(tmp_path, "--dry-run", state=TWO_BRIDGES)
    assert dry.returncode == 0, dry.stderr
    # One line an object of the list: two roles, the pool, the user, the token and ten grants.
    assert len(dry.stdout.splitlines()) == len(access.objects(_host())) == 15
    assert "the token's line prints JSON whose \"value\" is the token's secret" in dry.stderr
    listed = _run(tmp_path, "--list")
    assert (listed.returncode, listed.stdout) == (0, "storage local-lvm\nbridge vmbr0\nbridge vmbr1\n"), listed.stderr
    assert _writes(tmp_path) == []


def test_every_printed_token_id_is_quoted_and_the_token_s_line_asks_for_json(tmp_path: Path) -> None:
    lines = _run(tmp_path, "--dry-run").stdout.splitlines()
    named = [line for line in lines if "!" in line]
    assert len(named) == 5 and all(f"'{TOKEN_ID}'" in line for line in named)
    (token,) = [line for line in lines if line.startswith("pveum user token add")]
    assert token.endswith("--privsep 1 --comment agent-warehouse --output-format json")


def test_under_pool_nothing_is_granted_on_the_guests_outside_it(tmp_path: Path) -> None:
    result = _run(tmp_path, "--audit-scope", "pool")
    assert result.returncode == 0, result.stderr
    state = _state(tmp_path)
    assert not [a for a in state["acls"] if a["path"] == "/vms" or a["path"].startswith("/vms/")]
    assert access.AUDIT_ROLE not in state["roles"]


def test_a_grant_that_fails_after_the_token_says_its_delete_line_and_prints_no_secret(tmp_path: Path) -> None:
    result = _run(tmp_path, state={"fail": "/vms --tokens"})
    assert (result.returncode, result.stdout) == (1, ""), result.stderr
    assert SECRET not in result.stderr
    assert "after the token was made: it is there with its secret unsaid" in result.stderr
    assert "As root here, pveum user token delete agent-warehouse@pve aw deletes it" in result.stderr
    # Deleted as said, a run again makes it anew.
    pve = tmp_path / "pve"
    subprocess.run([str(pve / "pveum"), "user", "token", "delete", access.USER, access.TOKEN], check=True)
    state = _state(tmp_path) | {"fail": "", "next_secret": OTHER}
    (pve / "pve-state.json").write_text(json.dumps(state))
    again = _run(tmp_path)
    assert (again.returncode, again.stdout) == (0, OTHER + "\n"), again.stderr


# ----------------------------------------------------------------- refusals ---
def _refused(tmp_path: Path, *args: str, state: dict | None = None, **run: object) -> subprocess.CompletedProcess:
    """A run that must change nothing: refused, nothing on stdout, the host byte for byte as it was."""
    _install(tmp_path, state)
    before = _state_bytes(tmp_path)
    result = _run(tmp_path, *args, **run)  # type: ignore[arg-type]
    assert (result.returncode, result.stdout) == (1, ""), result.stderr
    assert _state_bytes(tmp_path) == before
    assert _writes(tmp_path) == []
    return result


def test_it_runs_only_as_root(tmp_path: Path) -> None:
    result = _refused(tmp_path, state={"uid": 1000})
    assert "run it as root, on the Proxmox host" in result.stderr
    assert [call[0] for call in _pve_cli_calls(tmp_path / "pve")] == ["id"]


def test_a_host_below_proxmox_ve_9_is_refused_by_name(tmp_path: Path) -> None:
    result = _refused(tmp_path, state={"version": "8.2.4"})
    assert "this is Proxmox VE 8, and the engine needs Proxmox VE 9 or later" in result.stderr


@pytest.mark.parametrize(
    ("args", "option"),
    [
        (("--pool", "fleet/dev"), "--pool"),
        (("--pool", "1fleet"), "--pool"),
        (("--pool", "dev vms"), "--pool"),
        (("--pool", "dev:vms"), "--pool"),
        (("--user", "automation"), "--user"),
        (("--user", "a" * (access.USER_MOST - 3) + "@pve"), "--user"),
        (("--token", "7"), "--token"),
        (("--token", "a!b"), "--token"),
        (("--storage", "local\nlvm"), "--storage"),
        (("--storage", "loſal"), "--storage"),
        (("--bridge", "vmbr0;id"), "--bridge"),
        (("--audit-scope", "node pool"), "--audit-scope"),
    ],
)
def test_a_value_of_the_wrong_form_is_refused_before_any_pveum_and_never_said_back(tmp_path, args, option) -> None:
    result = _refused(tmp_path, *args)
    assert result.stderr.startswith("error: ") and result.stderr.rstrip().endswith(f"({option})")
    assert args[1] not in result.stderr
    assert _pve_cli_calls(tmp_path / "pve") == []


def test_root_at_pam_is_refused_as_the_token_s_user(tmp_path: Path) -> None:
    result = _refused(tmp_path, "--user", "root@pam")
    assert "never root@pam, which holds every privilege (--user)" in result.stderr
    assert _pve_cli_calls(tmp_path / "pve") == []


@pytest.mark.parametrize("words", [("--move", "dev"), ("--template", "9000"), ("--token-secret",), ("extra",)])
def test_an_option_the_script_does_not_have_is_refused(tmp_path: Path, words) -> None:
    result = _refused(tmp_path, *words)
    assert "the options are at the top of this script" in result.stderr
    assert _pve_cli_calls(tmp_path / "pve") == []


def test_a_token_made_without_privilege_separation_is_refused_with_plan_s_sentence(tmp_path: Path) -> None:
    state = {"users": {"root@pam": {}, access.USER: {}}, "tokens": {TOKEN_ID: {"privsep": 0, "secret": SECRET}}}
    result = _refused(tmp_path, state=state)
    with pytest.raises(access.NotOwn) as refusal:
        _planned(_host(), _state(tmp_path))
    assert str(refusal.value) in result.stderr.splitlines()


# --------------------------------------------------------------- the pool's mark ---
# Comments of a pool named so, each with what fleet/mark.py and plan make of it.
COMMENTS = {
    "this fleet's": MARK,
    "beside a note": f"{NOTE} {MARK}",
    "on a line of its own": f"{NOTE}\n{MARK}\nmore",
    "after a separator Python reads as a space": f"x\x1c{MARK}\x1f",
    "after a no-break space": f"x\xa0{MARK}",
    "after a line separator": f"x {MARK}",
    "another fleet's": f"agent-warehouse fleet={OTHER} controller=none",
    "a control VM's": f"agent-warehouse fleet={FLEET} controller={CONTROLLER}",
    "twice": f"{MARK} {MARK}",
    "begun twice": f"{MARK} agent-warehouse\tcontroller=",
    "two spaces in it": f"agent-warehouse  fleet={FLEET} controller=none",
    "joined to a word": f"x{MARK}",
    "after a zero-width space": f"x​{MARK}",
    "broken by a separator Perl reads as no space": f"agent-warehouse\x1dfleet={FLEET} controller=none",
    "an upper-case ID": f"agent-warehouse fleet={FLEET.upper()} controller=none",
    "a word missing": "agent-warehouse controller=none",
    "no mark": NOTE,
    "none at all": "",
}


@pytest.mark.parametrize("comment", COMMENTS.values(), ids=COMMENTS)
def test_a_pool_s_comment_is_read_as_fleet_mark_reads_it(tmp_path: Path, comment: str) -> None:
    _install(tmp_path, {"pools": {access.POOL: {"comment": comment} if comment else {}}})
    try:
        _planned(_host(), _state(tmp_path))
    except access.NotOwn as refusal:
        result = _refused(tmp_path, "--dry-run")
        assert str(refusal) in result.stderr.splitlines()
    else:
        result = _run(tmp_path, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert not [line for line in result.stdout.splitlines() if line.startswith("pveum pool")]


@pytest.mark.parametrize(
    ("comment", "taken"),
    [(NOTE, f"{NOTE} {MARK}"), (f"{NOTE}\n", f"{NOTE}\n{MARK}"), (f"{NOTE}\x1c", f"{NOTE}\x1c{MARK}"), ("", MARK)],
    ids=["a note", "a note and a newline", "a separator last", "no comment"],
)
def test_a_pool_with_no_mark_is_taken_only_with_pool_and_take_unmarked(tmp_path: Path, comment, taken) -> None:
    state = {"pools": {access.POOL: {"comment": comment} if comment else {}, "other": {"comment": NOTE}}}
    refused = _refused(tmp_path, state=state)
    assert mark.may_change(None, record.first(FLEET)) in refused.stderr.splitlines()
    assert f"run this again with --pool {access.POOL} --take-unmarked." in refused.stderr
    dry = _run(tmp_path, "--pool", access.POOL, "--take-unmarked", "--dry-run")
    assert dry.returncode == 0, dry.stderr
    # The comment may hold a line's end: the command is the first of the lines, whole.
    assert dry.stdout.startswith(shlex.join(["pveum", "pool", "modify", access.POOL, "--comment", taken]) + "\n")
    result = _run(tmp_path, "--pool", access.POOL, "--take-unmarked")
    assert result.returncode == 0, result.stderr
    state = _state(tmp_path)
    assert state["pools"] == {access.POOL: {"comment": taken}, "other": {"comment": NOTE}}
    assert _planned(_host(), state).steps == ()


@pytest.mark.parametrize(
    ("args", "comment"),
    [
        (("--take-unmarked",), NOTE),
        (("--pool", access.POOL, "--take-unmarked"), MARK),
        (("--pool", access.POOL, "--take-unmarked"), f"agent-warehouse fleet={OTHER} controller=none"),
        (("--pool", access.POOL, "--take-unmarked"), f"{MARK} {MARK}"),
    ],
    ids=["without --pool", "this fleet's", "another fleet's", "a mark twice"],
)
def test_take_unmarked_without_pool_or_for_a_marked_pool_is_refused(tmp_path: Path, args, comment) -> None:
    result = _refused(tmp_path, *args, state={"pools": {access.POOL: {"comment": comment}}})
    assert "--take-unmarked" in result.stderr.splitlines()[-1]


def test_a_pool_another_fleet_marked_is_refused_with_its_fleet_named(tmp_path: Path) -> None:
    result = _refused(tmp_path, state={"pools": {access.POOL: {"comment": COMMENTS["another fleet's"]}}})
    assert f"error: the pool's mark names another fleet, {OTHER}, where" in result.stderr


# ------------------------------------------------------- the person's template ---
@pytest.mark.parametrize(
    ("guests", "config", "said"),
    [
        ([], CONFIG, "no template named my-template is on node pve"),
        ([GUEST, GUEST | {"vmid": TEMPLATE + 1}], CONFIG, "no template named my-template is on node pve"),
        ([GUEST | {"template": 0}], CONFIG, "no template named my-template is on node pve"),
        ([GUEST | {"node": "pve2"}], CONFIG, "no template named my-template is on node pve"),
        ([GUEST], {"net0": "virtio=BC:24:11:00:00:01,bridge=vmbr9"}, "on bridge vmbr9, which is not on node pve"),
        ([GUEST], {"net0": "virtio=BC:24:11:00:00:01,bridge=vm br"}, "a bridge whose name is not one Proxmox gives"),
    ],
    ids=["none", "two", "not a template", "another node", "a bridge not here", "a bridge of no such form"],
)
def test_a_person_s_template_is_the_one_guest_of_its_name_a_template_on_the_node(tmp_path, guests, config, said):
    result = _refused(tmp_path, state={"guests": guests}, custom="my-template", config=config)
    assert said in result.stderr


def test_the_renderer_takes_a_template_by_its_name_and_the_base_bridge_alone() -> None:
    with pytest.raises(ValueError, match="by the name given as custom"):
        access.as_script(_host(custom_template=TEMPLATE))
    with pytest.raises(ValueError, match="by the name given as custom"):
        access.as_script(_host(bridges=("vmbr0", "vmbr1")))
    for name in ("my template", "-template", "t" * 64, "tmpl\n", "t'"):
        with pytest.raises(access.Refused, match="proxmox_template_custom"):
            access.as_script(_host(), name)
    assert access.as_script(_host(), "my-template") == access.as_script(_host(), "my-template")


def test_the_header_names_init_s_lines_as_the_printer_writes_them() -> None:
    for host_flag in ("", "nuc"):
        rendered = access.as_script(_host(), host_flag=host_flag).splitlines()
        printed = printer.line("init", {"print_commands": True}, refused=True, host_flag=host_flag)
        assert f"#   {printed} | ssh root@<host> sh -s -- --dry-run" in rendered
        assert f"#   {printed} | ssh root@<host> sh -s" in rendered
        assert HEADER.search(rendered[1])
    assert "--host nuc" in access.as_script(_host(), host_flag="nuc").splitlines()[1]
    with pytest.raises(ValueError, match="host_flag"):
        access.as_script(_host(), host_flag="no such/host")


# ------------------------------------------------------------------ V2-9 ---
def test_no_file_of_the_checkout_holds_a_rendering_of_the_list() -> None:
    # The header the scan finds, which every rendering carries, for the node and the fleet it was rendered for.
    rendered = access.as_script(_host())
    assert HEADER.search(rendered)
    assert rendered.splitlines()[1].startswith(f"# Rendered by aw init for node pve of fleet {FLEET}")
    holding = [str(path) for path, text in repo_texts(untracked=True) if HEADER.search(text)]
    assert holding == []
