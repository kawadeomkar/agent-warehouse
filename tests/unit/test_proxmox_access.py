"""tools/proxmox-access.sh, the API access just init offers to set up: run against stand-in commands, never a real host.

The stand-ins (tests/molecule/fake_pve_cli.py) keep a host's roles, users, ACLs
and tokens in a file of the test's own and answer as pveum and pvesh do, and
the script runs under /bin/sh with them first on PATH - dash on a Linux runner,
as on the Proxmox host.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import REPO, _pve_cli, _pve_cli_calls, _pve_cli_state, _pve_cli_writes

SCRIPT = REPO / "tools" / "proxmox-access.sh"
SECRET = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CHECKED = yaml.safe_load((REPO / "playbooks" / "init_check.yml").read_text())[0]["vars"]["init_token_privileges"]
# And Sys.Audit, read only, on the node: its CPU threads and memory for just status's capacity, which init_check.yml
# does not require.
ROLE = [*CHECKED["vms"], "VM.GuestAgent.Audit", *CHECKED["storage"], *CHECKED["bridge"], "Sys.Audit"]
PLAN = [
    f"pveum role add AnsibleVM --privs {','.join(ROLE)}",
    "pveum user add ansible@pve --comment agent-warehouse",
    "pveum acl modify /vms --users ansible@pve --roles AnsibleVM",
    "pveum acl modify /storage/local-lvm --users ansible@pve --roles AnsibleVM",
    "pveum acl modify /sdn/zones/localnetwork/vmbr0 --users ansible@pve --roles AnsibleVM",
    "pveum acl modify /nodes/pve --users ansible@pve --roles AnsibleVM",
    "pveum user token add ansible@pve ansible --privsep 0 --comment agent-warehouse",
]


def _access(tmp_path: Path, *args: str, state: dict | None = None, path: str | None = None):
    """The script with args, the stand-ins in tmp_path/pve - made with state on the first call - first on PATH."""
    host = tmp_path / "pve"
    if not (host / "pve-state.json").exists():
        _pve_cli(host, {"next_secret": SECRET} | (state or {}))
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *args],
        env={"PATH": path or f"{host}{os.pathsep}/usr/bin{os.pathsep}/bin", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _writes(tmp_path: Path) -> list[str]:
    """The calls that changed the stand-in host."""
    return _pve_cli_writes(tmp_path / "pve")


def _acls(tmp_path: Path) -> list[tuple]:
    return sorted((a["path"], a["ugid"], a["roleid"], a["propagate"]) for a in _pve_cli_state(tmp_path / "pve")["acls"])


# --------------------------------------------------------------- a fresh host ---
def test_a_fresh_host_gets_the_role_user_grants_and_token_and_the_secret_alone_on_stdout(tmp_path: Path) -> None:
    result = _access(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == SECRET + "\n"
    assert SECRET not in result.stderr
    state = _pve_cli_state(tmp_path / "pve")
    assert state["roles"]["AnsibleVM"] == ROLE
    assert state["users"]["ansible@pve"] == {"comment": "agent-warehouse"}
    assert _acls(tmp_path) == [
        ("/nodes/pve", "ansible@pve", "AnsibleVM", 1),
        ("/sdn/zones/localnetwork/vmbr0", "ansible@pve", "AnsibleVM", 1),
        ("/storage/local-lvm", "ansible@pve", "AnsibleVM", 1),
        ("/vms", "ansible@pve", "AnsibleVM", 1),
    ]
    assert state["tokens"]["ansible@pve!ansible"]["privsep"] == 0
    # Each command is said as it runs, and the token is made last.
    assert [line[2:] for line in result.stderr.splitlines() if line.startswith("+ ")] == PLAN
    assert _writes(tmp_path)[-1].startswith("pveum user token add")


def test_the_dry_run_prints_the_commands_and_changes_nothing(tmp_path: Path) -> None:
    result = _access(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == PLAN
    assert _writes(tmp_path) == []
    assert "AnsibleVM" not in _pve_cli_state(tmp_path / "pve")["roles"]


def test_a_second_run_changes_nothing_and_says_the_token_exists(tmp_path: Path) -> None:
    assert _access(tmp_path).returncode == 0
    before = len(_writes(tmp_path))
    for args in ((), ("--dry-run",)):
        result = _access(tmp_path, *args)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert "token ansible@pve!ansible exists" in result.stderr and "pveum user token delete" in result.stderr
        assert "Nothing to do: the access is set up." in result.stderr
    assert len(_writes(tmp_path)) == before


@pytest.mark.parametrize(("version", "agent"), [("8.2.4", "VM.Monitor"), ("9.2.13", "VM.GuestAgent.Audit")])
def test_the_guest_agents_privilege_is_the_versions(tmp_path: Path, version: str, agent: str) -> None:
    # The stand-in refuses a privilege its version does not have, as Proxmox does.
    result = _access(tmp_path, state={"version": version})
    assert result.returncode == 0, result.stderr
    role = _pve_cli_state(tmp_path / "pve")["roles"]["AnsibleVM"]
    assert agent in role and ({"VM.Monitor", "VM.GuestAgent.Audit"} - {agent}).isdisjoint(role)


def test_list_prints_the_storages_and_bridges_and_changes_nothing(tmp_path: Path) -> None:
    state = {
        "storages": [
            {"storage": "local-zfs", "type": "zfspool", "content": "images,rootdir"},
            {"storage": "local", "type": "dir", "content": "iso,backup"},
            {"storage": "tank", "type": "nfs", "content": "images"},
        ],
        "bridges": ["vmbr0", "vmbr1"],
    }
    result = _access(tmp_path, "--list", state=state)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["storage local-zfs", "storage tank", "bridge vmbr0", "bridge vmbr1"]
    assert _writes(tmp_path) == []


# ------------------------------------------------------- the storage and bridge ---
def test_a_storage_the_node_lacks_is_refused_naming_those_it_has(tmp_path: Path) -> None:
    storages = [{"storage": "local-zfs", "type": "zfspool", "content": "images"}]
    storages += [{"storage": "tank", "type": "nfs", "content": "images"}]
    result = _access(tmp_path, state={"storages": storages})
    assert result.returncode == 1
    assert "storage local-lvm is not on node pve for VM disks: give one of local-zfs, tank with --storage" in (
        result.stderr
    )
    assert result.stdout == "" and _writes(tmp_path) == []
    result = _access(tmp_path, "--storage", "tank")
    assert result.returncode == 0, result.stderr
    assert ("/storage/tank", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)


def test_a_bridge_the_node_lacks_is_refused(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"bridges": ["vmbr1"]})
    assert result.returncode == 1
    assert "bridge vmbr0 is not on node pve: give one of vmbr1 with --bridge" in result.stderr
    assert _writes(tmp_path) == []
    assert _access(tmp_path, "--bridge", "vmbr1").returncode == 0
    assert ("/sdn/zones/localnetwork/vmbr1", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)


def test_a_node_with_no_storage_for_vm_disks_says_so(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"storages": [{"storage": "local", "type": "dir", "content": "iso"}]})
    assert result.returncode == 1 and "node pve has no storage for VM disks enabled" in result.stderr


# ------------------------------------------------------------ what exists ---
def test_a_role_that_exists_without_a_privilege_is_given_it_and_keeps_the_rest(tmp_path: Path) -> None:
    # A role an older version made lacks what a newer one needs: added to it, never in place of what it holds.
    lacking = [p for p in ROLE if p != "VM.Clone"]
    planned = _access(tmp_path, "--dry-run", state={"roles": {"AnsibleVM": [*lacking, "Sys.Console"]}})
    assert "pveum role modify AnsibleVM --append 1 --privs VM.Clone" in planned.stdout.splitlines()
    assert _writes(tmp_path) == []
    result = _access(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "+ pveum role modify AnsibleVM --append 1 --privs VM.Clone" in result.stderr
    assert _pve_cli_state(tmp_path / "pve")["roles"]["AnsibleVM"] == [*lacking, "Sys.Console", "VM.Clone"]


def test_a_role_an_older_version_made_gets_what_it_lacks_and_its_grant_and_no_token(tmp_path: Path) -> None:
    # The NUC's, made before Sys.Audit joined the role (live, 2026-11-13): --no-token, as just init runs it while its
    # vault holds the token's secret, adds the privilege and the node's grant, and says nothing of a token.
    state = {
        "roles": {"AnsibleVM": [p for p in ROLE if p != "Sys.Audit"]},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [
            {"path": path, "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 1, "type": "user"}
            for path in ("/vms", "/storage/local-lvm", "/sdn/zones/localnetwork/vmbr0")
        ],
        "tokens": {"ansible@pve!ansible": {"privsep": 0}},
    }
    commands = [
        "pveum role modify AnsibleVM --append 1 --privs Sys.Audit",
        "pveum acl modify /nodes/pve --users ansible@pve --roles AnsibleVM",
    ]
    planned = _access(tmp_path, "--dry-run", "--no-token", state=state)
    assert planned.returncode == 0 and planned.stdout.splitlines() == commands, planned.stderr
    result = _access(tmp_path, "--no-token")
    assert result.returncode == 0 and result.stdout == "", result.stderr
    assert _writes(tmp_path) == commands and "token" not in result.stderr
    assert _pve_cli_state(tmp_path / "pve")["roles"]["AnsibleVM"][-1] == "Sys.Audit"


def _set_up(**changes) -> dict:
    """A host the script set up, the role's grants on each path and its token there, with changes."""
    return {
        "roles": {"AnsibleVM": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [
            {"path": path, "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 1, "type": "user"}
            for path in ("/vms", "/storage/local-lvm", "/sdn/zones/localnetwork/vmbr0", "/nodes/pve")
        ],
        "tokens": {"ansible@pve!ansible": {"privsep": 0}},
    } | changes


def test_a_privilege_the_role_gains_where_it_is_granted_is_planned_on_the_role_alone(tmp_path: Path) -> None:
    # The upgrade to Proxmox VE 9: the role, granted on /vms, lacks VM.GuestAgent.Audit. Given to the role, it reaches
    # the user through that grant, so the dry run shows the one command the run makes, never a grant it then finds
    # made already (RP-6) - and so for a grant above the path, which propagates to it.
    command = ["pveum role modify AnsibleVM --append 1 --privs VM.GuestAgent.Audit"]
    state = _set_up(roles={"AnsibleVM": [p for p in ROLE if p != "VM.GuestAgent.Audit"]})
    planned = _access(tmp_path, "--dry-run", "--no-token", state=state)
    assert planned.returncode == 0 and planned.stdout.splitlines() == command, planned.stderr
    assert _access(tmp_path, "--no-token").returncode == 0
    assert _writes(tmp_path) == command
    above = tmp_path / "above"
    nodes = {"path": "/nodes", "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 1, "type": "user"}
    state = _set_up(
        roles={"AnsibleVM": [p for p in ROLE if p != "Sys.Audit"]},
        acls=[*(acl for acl in _set_up()["acls"] if acl["path"] != "/nodes/pve"), nodes],
    )
    planned = _access(above, "--dry-run", "--no-token", state=state)
    assert planned.stdout.splitlines() == ["pveum role modify AnsibleVM --append 1 --privs Sys.Audit"], planned.stderr


@pytest.mark.parametrize(
    ("deeper", "run"),
    [
        # Another role of the user's own on the node takes the place of the one propagating from /nodes there.
        pytest.param({"path": "/nodes/pve", "ugid": "ansible@pve", "roleid": "Watcher", "type": "user"}, 2, id="own"),
        # NoAccess beside the role cancels it.
        pytest.param({"path": "/nodes", "ugid": "ansible@pve", "roleid": "NoAccess", "type": "user"}, 2, id="barred"),
        # A group's grant on the node may be the user's, which the listing does not say: shown, and here not needed.
        pytest.param({"path": "/nodes/pve", "ugid": "ops", "roleid": "Watcher", "type": "group"}, 1, id="group"),
    ],
)
def test_a_grant_above_counts_only_while_nothing_deeper_replaces_it(tmp_path: Path, deeper: dict, run: int) -> None:
    # Proxmox takes a user's roles from the deepest path on the way that grants it any, in place of those above. So
    # the role's grant on /nodes, which propagates, does not bring its new privilege to the node where something
    # deeper is granted: the dry run shows the node's grant, which the run then makes (RP-8) - or, below a group's,
    # finds made already: the question may show more than the run makes, never less.
    commands = [
        "pveum role modify AnsibleVM --append 1 --privs Sys.Audit",
        "pveum acl modify /nodes/pve --users ansible@pve --roles AnsibleVM",
    ]
    above = {"path": "/nodes", "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 1, "type": "user"}
    state = _set_up(
        roles={"AnsibleVM": [p for p in ROLE if p != "Sys.Audit"], "Watcher": ["VM.Audit"], "NoAccess": []},
        acls=[*(acl for acl in _set_up()["acls"] if acl["path"] != "/nodes/pve"), above, {"propagate": 1} | deeper],
    )
    planned = _access(tmp_path, "--dry-run", "--no-token", state=state)
    assert planned.returncode == 0 and planned.stdout.splitlines() == commands, planned.stderr
    assert _access(tmp_path, "--no-token").returncode == 0
    assert _writes(tmp_path) == commands[:run]


def test_with_no_token_a_token_that_is_not_there_leaves_the_host_as_it_is(tmp_path: Path) -> None:
    # --no-token is just init's while its vault holds a secret, which can be only this token's: on a host installed
    # again, the role, user and grants it would add could never make that secret work, so nothing changes (RP-4).
    for args in (("--dry-run", "--no-token"), ("--no-token",)):
        result = _access(tmp_path, *args)
        assert result.returncode == 1 and result.stdout == "", result.stderr
        assert "token ansible@pve!ansible is not on node pve, so the secret just init holds is not its" in result.stderr
    assert _writes(tmp_path) == []


def test_with_no_token_a_token_made_with_privilege_separation_is_said(tmp_path: Path) -> None:
    # What its user is given reaches such a token only through a grant of its own, so status may go on finding it
    # short: said (RP-4), with both ways out, since a token granted the role itself works as it is (RP-9).
    state = _set_up(tokens={"ansible@pve!ansible": {"privsep": 1}})
    result = _access(tmp_path, "--dry-run", "--no-token", state=state)
    assert result.returncode == 0 and result.stdout == "", result.stderr
    assert "WARNING: token ansible@pve!ansible was made with privilege separation" in result.stderr
    assert "holds only what it is granted itself" in result.stderr and "holds none" not in result.stderr
    assert "grant the token the role wherever ansible@pve holds it, or remove it" in result.stderr


def test_a_user_that_holds_the_privileges_another_way_gets_no_grant(tmp_path: Path) -> None:
    # A host whose owner granted the user a role of their own, on / - the
    # homeserver's storage grant, say: nothing is added beside it.
    state = {
        "roles": {"Owners": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [{"path": "/", "ugid": "ansible@pve", "roleid": "Owners", "propagate": 1, "type": "user"}],
    }
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert not [write for write in _writes(tmp_path) if write.startswith("pveum acl")]
    assert "ansible@pve holds what it needs on /storage/local-lvm" in result.stderr
    assert result.stdout == SECRET + "\n"


def test_a_grant_that_does_not_propagate_from_above_is_no_grant(tmp_path: Path) -> None:
    state = {
        "roles": {"AnsibleVM": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [{"path": "/", "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 0, "type": "user"}],
    }
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert len([write for write in _writes(tmp_path) if write.startswith("pveum acl")]) == 4


def test_a_grant_on_vms_that_does_not_propagate_is_named_and_left(tmp_path: Path) -> None:
    # Proxmox checks each VM's own path, /vms/<id>: what is held on /vms alone is not enough, and the
    # grant there is the person's to change.
    state = {
        "roles": {"AnsibleVM": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "acls": [{"path": "/vms", "ugid": "ansible@pve", "roleid": "AnsibleVM", "propagate": 0, "type": "user"}],
    }
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert "ansible@pve holds VM.Allocate," in result.stderr and "on /vms but not on the VMs under it" in result.stderr
    assert "pveum acl modify /vms --users ansible@pve --roles AnsibleVM --propagate 1" in result.stderr
    grants = [write for write in _writes(tmp_path) if write.startswith("pveum acl")]
    granted = [grant.split()[3] for grant in grants]
    assert granted == ["/storage/local-lvm", "/sdn/zones/localnetwork/vmbr0", "/nodes/pve"]
    assert ("/vms", "ansible@pve", "AnsibleVM", 0) in _acls(tmp_path)


# ----------------------------------------------------------------- a pool ---
POOL_PLAN = [
    PLAN[0] + ",Pool.Audit",
    "pveum pool add agent-warehouse --comment agent-warehouse",
    PLAN[1],
    "pveum acl modify /pool/agent-warehouse --users ansible@pve --roles AnsibleVM",
    *PLAN[3:],
]


def test_with_a_pool_the_role_is_granted_on_the_pool_made_for_it_in_place_of_vms(tmp_path: Path) -> None:
    dry = _access(tmp_path, "--pool", "agent-warehouse", "--dry-run")
    assert dry.returncode == 0 and dry.stdout.splitlines() == POOL_PLAN, dry.stderr
    assert _writes(tmp_path) == []
    result = _access(tmp_path, "--pool", "agent-warehouse")
    assert result.returncode == 0 and result.stdout == SECRET + "\n", result.stderr
    assert [line[2:] for line in result.stderr.splitlines() if line.startswith("+ ")] == POOL_PLAN
    assert _pve_cli_state(tmp_path / "pve")["pools"] == {"agent-warehouse": {"comment": "agent-warehouse"}}
    assert [acl[0] for acl in _acls(tmp_path)] == [
        "/nodes/pve",
        "/pool/agent-warehouse",
        "/sdn/zones/localnetwork/vmbr0",
        "/storage/local-lvm",
    ]
    # Again: the pool is there, and nothing changes.
    before = len(_writes(tmp_path))
    again = _access(tmp_path, "--pool", "agent-warehouse")
    assert again.returncode == 0 and "pool agent-warehouse exists" in again.stderr, again.stderr
    assert len(_writes(tmp_path)) == before


def test_with_a_pool_a_grant_on_vms_held_already_is_left_and_how_to_take_it_away_said(tmp_path: Path) -> None:
    # A fleet made before the pool is reached through /vms until root makes each VM a member: never taken away here.
    assert _access(tmp_path).returncode == 0
    result = _access(tmp_path, "--pool", "agent-warehouse", "--no-token")
    assert result.returncode == 0 and result.stdout == "", result.stderr
    assert ("/vms", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)
    assert ("/pool/agent-warehouse", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)
    assert "ansible@pve still holds role AnsibleVM on /vms, which reaches every VM of the host" in result.stderr
    assert "pveum pool modify agent-warehouse --vms <vmid>,<vmid>" in result.stderr
    assert "pveum acl delete /vms --users ansible@pve --roles AnsibleVM" in result.stderr
    # Without the grant on /vms, nothing about it is said.
    alone = tmp_path / "alone"
    alone.mkdir()
    fresh = _access(alone, "--pool", "agent-warehouse")
    assert fresh.returncode == 0 and "still holds" not in fresh.stderr, fresh.stderr


def test_with_a_pool_a_grant_on_it_that_does_not_propagate_is_named_and_left(tmp_path: Path) -> None:
    state = {
        "roles": {"AnsibleVM": ROLE},
        "users": {"root@pam": {}, "ansible@pve": {}},
        "pools": {"agent-warehouse": {"comment": ""}},
        "acls": [
            {
                "path": "/pool/agent-warehouse",
                "ugid": "ansible@pve",
                "roleid": "AnsibleVM",
                "propagate": 0,
                "type": "user",
            }
        ],
    }
    result = _access(tmp_path, "--pool", "agent-warehouse", state=state)
    assert result.returncode == 0, result.stderr
    assert "on /pool/agent-warehouse but not on the VMs under it" in result.stderr
    assert "pveum acl modify /pool/agent-warehouse --users ansible@pve --roles AnsibleVM --propagate 1" in result.stderr
    assert ("/pool/agent-warehouse", "ansible@pve", "AnsibleVM", 0) in _acls(tmp_path)


FLEET = [
    {"vmid": 101, "name": "alpha", "template": 0, "tags": "agent-warehouse"},
    {"vmid": 102, "name": "beta", "template": 0, "tags": "agent-warehouse;agent-warehouse-sized"},
    {"vmid": 103, "name": "agent-warehouse-golden-1a2b", "template": 1},
    {"vmid": 104, "name": "old", "template": 0, "tags": "claude-on-proxmox"},
    {"vmid": 9000, "name": "ubuntu-24.04-cloudinit", "template": 1},
    {"vmid": 300, "name": "talos-cp", "template": 0, "tags": "k8s;talos"},
    {"vmid": 301, "name": "agent-warehouse-x", "template": 0, "tags": "agent-warehouse-tailscale"},
]
MOVE = ["--pool", "agent-warehouse", "--move", "agent-warehouse,claude-on-proxmox", "--no-token"]


def test_with_move_the_fleet_made_before_the_pool_goes_in_by_name_and_then_the_grant_on_vms_goes(
    tmp_path: Path,
) -> None:
    assert _access(tmp_path, state={"guests": FLEET}).returncode == 0
    dry = _access(tmp_path, *MOVE, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    lines = dry.stdout.splitlines()
    # Each guest of the fleet is named: the tagged VMs, the 1.x tag's too, the golden and the template; never another
    # tag that only starts as the tag does, nor a guest of another tag.
    assert (
        "pveum pool modify agent-warehouse --vms 101,102,103,104,9000   # alpha (101), beta (102),"
        " agent-warehouse-golden-1a2b (103), old (104), ubuntu-24.04-cloudinit (9000)"
    ) in lines
    assert lines[-1] == "pveum acl delete /vms --users ansible@pve --roles AnsibleVM"
    run = _access(tmp_path, *MOVE)
    assert run.returncode == 0 and run.stdout == "", run.stderr
    pools = {g["vmid"]: g.get("pool") for g in _pve_cli_state(tmp_path / "pve")["guests"]}
    assert pools == {
        101: "agent-warehouse",
        102: "agent-warehouse",
        103: "agent-warehouse",
        104: "agent-warehouse",
        9000: "agent-warehouse",
        300: None,
        301: None,
    }
    assert [acl[0] for acl in _acls(tmp_path)] == [
        "/nodes/pve",
        "/pool/agent-warehouse",
        "/sdn/zones/localnetwork/vmbr0",
        "/storage/local-lvm",
    ]
    # Again: nothing outside, nothing on /vms, nothing to do.
    again = _access(tmp_path, *MOVE, "--dry-run")
    assert again.returncode == 0 and again.stdout == "", again.stdout
    assert "still holds" not in again.stderr


def test_with_move_a_guest_in_another_pool_is_left_and_so_is_the_grant_on_vms(tmp_path: Path) -> None:
    guests = [*FLEET[:2], {"vmid": 105, "name": "theirs", "template": 0, "tags": "agent-warehouse", "pool": "bob"}]
    assert _access(tmp_path, state={"guests": guests}).returncode == 0
    result = _access(tmp_path, *MOVE)
    assert result.returncode == 0, result.stderr
    assert "left where they are, in a pool of their own: theirs (105, in pool bob)" in result.stderr
    assert ("/vms", "ansible@pve", "AnsibleVM", 1) in _acls(tmp_path)
    pools = {g["vmid"]: g.get("pool") for g in _pve_cli_state(tmp_path / "pve")["guests"]}
    assert pools == {101: "agent-warehouse", 102: "agent-warehouse", 105: "bob"}


def test_a_privilege_the_role_lacked_is_granted_once_the_role_has_it(tmp_path: Path) -> None:
    # A role made without SDN.Use: given it first, then granted on the bridge - never said to be held there before.
    state = {"roles": {"AnsibleVM": [p for p in ROLE if p != "SDN.Use"]}}
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    writes = _writes(tmp_path)
    assert writes[0] == "pveum role modify AnsibleVM --append 1 --privs SDN.Use"
    assert "pveum acl modify /sdn/zones/localnetwork/vmbr0 --users ansible@pve --roles AnsibleVM" in writes
    assert "holds what it needs on /sdn" not in result.stderr


def test_a_user_that_holds_privileges_on_root_is_warned_of_before_its_token(tmp_path: Path) -> None:
    state = {
        "roles": {"Everything": [*ROLE, "Sys.Modify", "Permissions.Modify"]},
        "users": {"root@pam": {}, "admin@pve": {}},
        "acls": [{"path": "/", "ugid": "admin@pve", "roleid": "Everything", "propagate": 1, "type": "user"}],
    }
    for args in (("--dry-run",), ()):
        result = _access(tmp_path, "--user", "admin@pve", *args, state=state)
        assert result.returncode == 0, result.stderr
        assert "WARNING: admin@pve holds" in result.stderr and "Permissions.Modify" in result.stderr
    # A user this run makes holds nothing on /.
    assert "WARNING" not in _access(tmp_path / "fresh").stderr


def test_a_token_with_privilege_separation_is_named_and_left(tmp_path: Path) -> None:
    state = {"users": {"root@pam": {}, "ansible@pve": {}}, "tokens": {"ansible@pve!ansible": {"privsep": 1}}}
    result = _access(tmp_path, state=state)
    assert result.returncode == 0, result.stderr
    assert "It was made with privilege separation, so it holds only what it is granted itself" in result.stderr
    assert result.stdout == ""
    assert _pve_cli_state(tmp_path / "pve")["tokens"]["ansible@pve!ansible"] == {"privsep": 1}


def test_another_user_and_token_are_used_as_given(tmp_path: Path) -> None:
    result = _access(tmp_path, "--user", "deploy@pve", "--token", "claude")
    assert result.returncode == 0, result.stderr
    assert "deploy@pve!claude" in _pve_cli_state(tmp_path / "pve")["tokens"]
    assert ("/vms", "deploy@pve", "AnsibleVM", 1) in _acls(tmp_path)


# ------------------------------------------------------------- refusals ---
def test_it_runs_only_as_root(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"uid": 1000})
    assert result.returncode == 1 and "run it as root, on the Proxmox host" in result.stderr
    assert [call[0] for call in _pve_cli_calls(tmp_path / "pve")] == ["id"]


def test_it_runs_only_where_pveum_is(tmp_path: Path) -> None:
    host = _pve_cli(tmp_path / "pve")
    alone = tmp_path / "id-alone"
    alone.mkdir()
    shutil.copy(host / "id", alone / "id")
    shutil.copy(host / "pve-state.json", alone / "pve-state.json")
    result = _access(tmp_path, path=f"{alone}{os.pathsep}/usr/bin{os.pathsep}/bin")
    assert result.returncode == 1 and "there is no pveum here" in result.stderr


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (["--storage", "local lvm"], "--storage may contain only"),
        (["--storage", "x;reboot"], "--storage may contain only"),
        (["--bridge", "$(reboot)"], "--bridge may contain only"),
        (["--bridge", ""], "--bridge may contain only"),
        (["--user", "ansible"], "--user is user@realm"),
        (["--user", "a@b@c"], "--user is user@realm"),
        (["--user", "@pve"], "--user is user@realm"),
        (["--user", "root@pam"], "never root@pam"),
        (["--token", "1st"], "--token starts with a letter"),
        (["--token"], "--token takes a value"),
        (["--pool", "-x"], "--pool starts with a letter or a digit"),
        (["--pool", "a pool"], "--pool may contain only"),
        (["--pool", "$(reboot)"], "--pool may contain only"),
        (["--move", "agent-warehouse"], "give --pool too"),
        (["--pool", "p", "--move", "a b"], "--move may contain only"),
        (["--template", "90x"], "--template is a VMID"),
        (["--force"], "no option --force"),
    ],
)
def test_a_value_that_is_not_one_plain_word_is_refused_before_anything_runs(
    tmp_path: Path, args: list[str], said: str
) -> None:
    result = _access(tmp_path, *args)
    assert result.returncode == 1 and said in result.stderr
    assert _pve_cli_calls(tmp_path / "pve") == []


def test_a_refused_write_stops_it_before_the_token(tmp_path: Path) -> None:
    result = _access(tmp_path, state={"fail": "acl modify /storage"})
    assert result.returncode != 0
    assert result.stdout == ""
    assert _pve_cli_state(tmp_path / "pve")["tokens"] == {}


# --------------------------------------------------------------- static ---
def test_the_privileges_are_the_ones_just_init_checks() -> None:
    text = SCRIPT.read_text()
    granted = {
        name: re.search(rf"^{name}_privileges='([^']+)'$", text, re.MULTILINE).group(1).split()
        for name in ("vms", "storage", "bridge", "pool")
    }
    assert granted == CHECKED
    assert "agent=VM.GuestAgent.Audit; else agent=VM.Monitor" in text


def test_the_commands_the_docs_show_are_the_ones_it_runs() -> None:
    # PLAN is what its dry run prints: test_the_dry_run_prints_the_commands_and_changes_nothing.
    page = (REPO / "docs" / "getting-started" / "proxmox-access.md").read_text()
    shown = re.search(r"it runs:\n\n```text\n(.*?)```", page, re.S).group(1).splitlines()
    assert shown == PLAN


def test_its_stdout_is_data_and_its_talk_is_stderr() -> None:
    # What a person reads goes through say or die, to stderr. What reaches
    # stdout, which just init reads, is a printf not piped on: the dry run's
    # commands (run's), --list's lines and the secret.
    text = SCRIPT.read_text()
    to_stdout = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\b(?:printf|echo)\b", line) and "|" not in line and ">&2" not in line
    ]
    assert to_stdout == [
        "for name in $storages; do printf 'storage %s\\n' \"$name\"; done",
        "for name in $bridges; do printf 'bridge %s\\n' \"$name\"; done",
        "printf '%s\\n' \"$*\"",
        'printf \'%s   # %s\\n\' "$*" "$note"',
        "printf '%s\\n' \"$secret\"",
    ], to_stdout
