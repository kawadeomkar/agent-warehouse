"""Every VM's SSH host key is held under the VM's identity, in the project's own known_hosts file: the options every
connection to a VM is made with (vm_ssh_args), the socket a shared connection to it is kept on (vm_control_name), and
the carry-over of a key kept by address before 2.6 (playbooks/tasks/host_keys.yml), run for real against known_hosts
files in a scratch directory - never the developer's ~/.ssh - whose user's file must come out byte for byte as it went
in."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import REPO, _failures, _results, _run

from claude_vm import checks

ALPHA = "agent-warehouse-0b5f5bbc-4a1f-4c1e-9d6b-6f1c2d3e4f50"
BETA = "agent-warehouse-11111111-2222-4333-8444-555555555555"
GAMMA = "agent-warehouse-99999999-2222-4333-8444-555555555555"
OMEGA = "agent-warehouse-33333333-2222-4333-8444-555555555555"
SAY = "host_keys | Say whose keys were copied, and whose are taken on first use"


def test_every_connection_names_the_project_s_file_and_the_vm_s_identity():
    args = shlex.split(checks.vm_ssh_args(ALPHA, "/home/me/.ssh/agent-warehouse.known_hosts"))
    assert args == [
        "-o",
        'UserKnownHostsFile="/home/me/.ssh/agent-warehouse.known_hosts"',
        "-o",
        "CheckHostIP=no",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        f"HostKeyAlias={ALPHA}",
        "-o",
        "ForwardAgent=no",
    ]


def test_forwarding_is_left_on_only_where_asked_and_a_vm_with_no_identity_is_filed_by_address():
    args = shlex.split(checks.vm_ssh_args("", "/k", forward_agent=True))
    assert "ForwardAgent=no" not in args and not any(arg.startswith("HostKeyAlias") for arg in args)
    assert 'UserKnownHostsFile="/k"' in args


def test_a_path_with_a_space_is_one_file_to_ssh():
    # Ansible splits the arguments as shlex does, and ssh takes an unquoted space in UserKnownHostsFile as the end of
    # one file: in double quotes, it is one.
    (option,) = [arg for arg in shlex.split(checks.vm_ssh_args(ALPHA, "/Users/me with space/k")) if "Known" in arg]
    assert option == 'UserKnownHostsFile="/Users/me with space/k"'
    resolved = subprocess.run(
        ["ssh", "-G", "-F", "/dev/null", "-o", option, "192.0.2.60"], capture_output=True, text=True, check=True
    ).stdout
    assert "userknownhostsfile /Users/me with space/k" in resolved


@pytest.mark.parametrize("alias", ["github.com", "agent-warehouse-x", f"{ALPHA}\n", f"{ALPHA} -oProxyCommand=x"])
def test_an_alias_that_is_not_one_of_ours_is_refused(alias):
    with pytest.raises(ValueError, match="is not a VM's host key alias"):
        checks.vm_ssh_args(alias, "/k")
    with pytest.raises(ValueError, match="is not a VM's host key alias"):
        checks.vm_control_name(alias, "192.0.2.60")


@pytest.mark.parametrize("path", ["", "/k\nProxyCommand x", '/k" -oProxyCommand=x "'])
def test_the_known_hosts_file_is_one_path_ssh_can_be_given(path):
    with pytest.raises(ValueError, match="no double quote or line break"):
        checks.vm_ssh_args(ALPHA, path)


def test_each_identity_has_a_shared_connection_of_its_own():
    # A VM that reports another's address would ride that one's open connection, on which no key is checked again.
    names = {checks.vm_control_name(alias, address) for alias in (ALPHA, BETA, "") for address in ("192.0.2.60", "::1")}
    assert len(names) == 6


def test_a_vm_reached_through_a_hop_has_a_shared_connection_of_the_hop_s_own():
    # Two Proxmox hosts with one VM range: a VM with no identity is named by its address, and through two hops that
    # would let a run against one host ride a connection opened through the other. Without a hop, today's name.
    hops = ("awh-0123456789ab", "awh-ba9876543210")
    assert len({checks.vm_control_name(ALPHA, "192.0.2.60", hop) for hop in hops}) == 2
    assert len({checks.vm_control_name("", "192.0.2.60", hop) for hop in hops}) == 2
    assert checks.vm_control_name(ALPHA, "192.0.2.60", "") == checks.vm_control_name(ALPHA, "192.0.2.60")
    assert checks.vm_control_name(ALPHA, "192.0.2.60", hops[0]) != checks.vm_control_name(ALPHA, "192.0.2.60")
    # The name's shape and length do not change, so the path below still fits.
    assert len(checks.vm_control_name(ALPHA, "192.0.2.60", hops[0])) == len(checks.vm_control_name(ALPHA, "192.0.2.60"))


def test_a_socket_s_path_leaves_a_long_account_name_room_on_macos():
    # ssh refuses a ControlPath longer than a socket's address holds, 103 characters on macOS, counting the 17 it adds
    # (".<16 random>") while it makes the socket, and Ansible's directory is in the account's home. ssh's own %C is 40
    # characters: beside a prefix of the identity's, it left an account name 12.
    name = checks.vm_control_name(ALPHA, "2001:db8:ffff:ffff:ffff:ffff:ffff:ffff") + "-%r-%p"
    socket = f"/Users/{'u' * 40}/.ansible/cp/{name}".replace("%r", "dev").replace("%p", "22")
    assert len(socket) + 17 <= 103, len(socket)


@pytest.mark.parametrize(
    ("args", "alias", "held"),
    [
        (checks.vm_ssh_args(ALPHA, "/k") + " -o ProxyJump=j", ALPHA, True),
        ("-o ProxyJump=j", ALPHA, False),
        (checks.vm_ssh_args(BETA, "/k"), ALPHA, False),
        (checks.vm_ssh_args("", "/other"), "", False),
        ("-o 'unbalanced", "", False),
    ],
)
def test_a_connection_that_lost_the_project_s_options_is_found(args, alias, held):
    hostvars = {"alpha": {"ansible_ssh_common_args": args, "claude_vm_host_key_alias": alias}}
    assert checks.vm_ssh_unheld([{"name": "alpha"}], hostvars, "/k") == ([] if held else ["alpha"])


def test_only_plain_key_lines_are_carried_over():
    stdout = "\n".join(
        [
            "# Host 192.0.2.10 found: line 3",
            "192.0.2.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
            "|1|abc=|def= ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY=",
            "@revoked 192.0.2.10 ssh-rsa AAAAB3NzaC1yc2E=",
            "192.0.2.10 ssh-dss AAAAB3NzaC1kc3M=",
            "192.0.2.10 ssh-ed25519 not;base64",
        ]
    )
    alpha, beta = {"name": "alpha", "key_alias": ALPHA}, {"name": "beta", "key_alias": BETA}
    results = [
        {"rc": 0, "stdout": stdout, "vm": alpha},
        {"rc": 1, "stdout": "", "vm": beta},
        {"skipped": True},
        {"rc": 0, "stdout": stdout, "vm": {"name": "x", "key_alias": "not-ours"}},
    ]
    assert checks.known_hosts_keys(results, "vm") == [
        {
            "name": "alpha",
            "key_alias": ALPHA,
            "key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
        },
        {"name": "alpha", "key_alias": ALPHA, "key": "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY="},
    ]


def test_a_carry_over_is_due_once_per_identity():
    vms = [
        {"name": "alpha", "address": "192.0.2.10", "key_alias": ALPHA},
        {"name": "beta", "address": "192.0.2.11", "key_alias": BETA},
        {"name": "gamma", "address": "192.0.2.12", "key_alias": GAMMA, "created": True},
        {"name": "delta", "address": "192.0.2.13", "key_alias": ""},
        {"name": "epsilon", "address": "", "key_alias": OMEGA},
    ]
    kept = [{"rc": 0, "claude_host_keys_vm": vms[1]}, {"rc": 1, "claude_host_keys_vm": vms[0]}]
    lines = [f"# agent-warehouse carried {OMEGA}", f"{BETA} ssh-ed25519 AAAA"]
    due = checks.known_hosts_due(vms, lines, kept)
    assert [(vm["name"], vm["created"]) for vm in due] == [("alpha", False), ("gamma", True)]
    assert checks.known_hosts_due(vms, [*lines, f"# agent-warehouse carried {ALPHA}"], kept)[0]["name"] == "gamma"


def test_ssh_arguments_split_as_ansible_splits_them():
    assert checks.ssh_args_split("-o ProxyJump=j -o 'UserKnownHostsFile=/a b'") == [
        "-o",
        "ProxyJump=j",
        "-o",
        "UserKnownHostsFile=/a b",
    ]
    assert checks.ssh_args_split(None) == []


def _key(path: Path) -> str:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return " ".join(path.with_suffix(".pub").read_text().split()[:2])


# A key kept by address before 2.6; another kept hashed; one already under its identity, whose old line by address
# differs and must not replace it; one with no identity; one with no address; and one this run created, whose address
# the user's file holds another machine's key for.
VMS = {
    "alpha": {"address": "192.0.2.10", "key_alias": ALPHA},
    "beta": {"address": "192.0.2.11", "key_alias": BETA},
    "gamma": {"address": "192.0.2.12", "key_alias": GAMMA},
    "delta": {"address": "192.0.2.13", "key_alias": ""},
    "epsilon": {"address": "", "key_alias": "agent-warehouse-22222222-2222-4333-8444-555555555555"},
    "omega": {"address": "192.0.2.14", "key_alias": OMEGA, "created": True},
}


def _carry(tmp_path: Path, ours: Path, users: Path, *args: str, common: str | None = None, vms: dict | None = None):
    vms = VMS if vms is None else vms
    play = {
        "name": "Publish and carry over",
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [
            {
                "name": "Publish",
                "ansible.builtin.add_host": {
                    "name": "{{ item.key }}",
                    "groups": "claude_vms",
                    "ansible_host": "{{ item.value.address }}",
                    "claude_vm_host_key_alias": "{{ item.value.key_alias }}",
                    "ansible_ssh_common_args": common
                    or "{{ item.value.key_alias | vm_ssh_args(claude_ssh_known_hosts_file) }}",
                },
                "loop": "{{ published | dict2items }}",
                "changed_when": False,
            },
            {
                "name": "Carry over",
                "ansible.builtin.import_tasks": str(REPO / "playbooks" / "tasks" / "host_keys.yml"),
                # The records the publishers pass: {name, address, key_alias, created}.
                "vars": {"claude_host_keys_vms": [{"name": name, **vm} for name, vm in vms.items()]},
            },
        ],
    }
    playbook = tmp_path / "carry.yml"
    playbook.write_text(yaml.safe_dump([play]))
    extra = {"published": vms, "claude_ssh_known_hosts_file": str(ours), "claude_ssh_user_known_hosts": str(users)}
    return _run(tmp_path, playbook, {}, "-e", json.dumps(extra), *args)


def _users(tmp_path: Path) -> tuple[Path, dict]:
    keys = {name: _key(tmp_path / name) for name in ("alpha", "beta", "gamma", "stale", "dead")}
    users = tmp_path / "known_hosts"
    users.write_text(
        f"192.0.2.10 {keys['alpha']}\n192.0.2.12 {keys['stale']}\n192.0.2.13 {keys['alpha']}\n"
        f"192.0.2.14 {keys['dead']}\n"
    )
    subprocess.run(["ssh-keygen", "-q", "-H", "-f", str(users)], check=True, capture_output=True)
    # ssh-keygen -H rewrites the file hashed, keeping its old self beside it: the user's file, hashed; and beta's key
    # by address appended unhashed after.
    users.write_text(users.read_text() + f"192.0.2.11 {keys['beta']}\n")
    return users, keys


def _kept(ours: Path) -> dict:
    return {
        line.split(" ", 1)[0]: line.split(" ", 1)[1]
        for line in ours.read_text().splitlines()
        if not line.startswith("#")
    }


def _marked(ours: Path) -> set:
    return {line.split()[-1] for line in ours.read_text().splitlines() if line.startswith("# agent-warehouse carried ")}


def _said(report: dict) -> str:
    return " ".join(str(result.get("msg", "")) for name, _, result in _results(report) if name == SAY)


def test_a_key_kept_by_address_is_copied_in_under_the_vm_s_identity_and_the_user_s_file_left_as_it_was(tmp_path):
    users, keys = _users(tmp_path)
    before = users.read_bytes()
    ours = tmp_path / "agent-warehouse.known_hosts"
    ours.write_text(f"{GAMMA} {keys['gamma']}\n")

    report = _carry(tmp_path, ours, users)
    assert report["rc"] == 0, report["stderr"][-3000:]
    assert users.read_bytes() == before
    # A VM this run created gets no key from the user's file: what it holds for the address is another machine's.
    assert _kept(ours) == {ALPHA: keys["alpha"], BETA: keys["beta"], GAMMA: keys["gamma"]}
    assert _marked(ours) == {ALPHA, BETA, OMEGA}
    said = _said(report)
    assert "Copied the SSH host keys of alpha, beta from " in said
    assert "the key this machine kept for the address each reports now" in said

    # Once: a second run finds every key or mark in place, and copies and writes nothing.
    again = _carry(tmp_path, ours, users)
    assert again["rc"] == 0 and again["stats"]["localhost"]["changed"] == 0
    assert _said(again) == ""


def test_a_key_removed_by_hand_is_never_put_back(tmp_path):
    # A VM renamed in Proxmox has new host keys: its old one is removed, and the user's line by address, which stays,
    # must not be copied back - its next connection would be refused again.
    users, keys = _users(tmp_path)
    ours = tmp_path / "agent-warehouse.known_hosts"
    assert _carry(tmp_path, ours, users)["rc"] == 0
    subprocess.run(["ssh-keygen", "-R", ALPHA, "-f", str(ours)], check=True, capture_output=True)
    assert ALPHA not in _kept(ours) and ALPHA in _marked(ours)
    again = _carry(tmp_path, ours, users)
    assert again["rc"] == 0, again["stderr"][-3000:]
    assert ALPHA not in _kept(ours)


def test_a_vm_with_nothing_kept_for_its_address_is_said_to_be_taken_on_first_use(tmp_path):
    users, _ = _users(tmp_path)
    ours = tmp_path / "agent-warehouse.known_hosts"
    vms = {"zeta": {"address": "192.0.2.99", "key_alias": ALPHA}}
    report = _carry(tmp_path, ours, users, vms=vms)
    assert report["rc"] == 0, report["stderr"][-3000:]
    said = _said(report)
    assert "zeta: " in said and "holds no key for the address it reports, so its key is taken on first use" in said
    assert _marked(ours) == {ALPHA}


def test_a_dry_run_carries_over_as_a_run_does(tmp_path):
    # --check connects as a run does, and ssh then takes on first use each key it finds no line for: the keys must be
    # there before, and the message true.
    users, keys = _users(tmp_path)
    ours = tmp_path / "agent-warehouse.known_hosts"
    ours.write_text(f"{GAMMA} {keys['gamma']}\n")
    report = _carry(tmp_path, ours, users, "--check")
    assert report["rc"] == 0, report["stderr"][-3000:]
    assert _kept(ours) == {ALPHA: keys["alpha"], BETA: keys["beta"], GAMMA: keys["gamma"]}
    assert "Copied the SSH host keys of alpha, beta from " in _said(report)


def test_a_vm_whose_options_were_outranked_is_refused_before_anything_connects(tmp_path):
    users, _ = _users(tmp_path)
    ours = tmp_path / "agent-warehouse.known_hosts"
    report = _carry(tmp_path, ours, users, common="-o ProxyJump=jump.invalid")
    assert report["rc"] != 0
    assert (
        "ansible_ssh_common_args is set for them somewhere that outranks the project's own"
        in (_failures(report)["localhost"])
    )
    assert not ours.exists()


def test_with_no_file_of_the_user_s_nothing_is_copied_and_nothing_fails(tmp_path):
    ours = tmp_path / "agent-warehouse.known_hosts"
    report = _carry(tmp_path, ours, tmp_path / "none")
    assert report["rc"] == 0, report["stderr"][-3000:]
    assert _kept(ours) == {} and _marked(ours) == {ALPHA, BETA, GAMMA, OMEGA}
