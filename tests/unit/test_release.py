"""Releasing a VM before just destroy deletes it: destroy.yml's release play and report, run for real.

Every host is the controller itself, over the local connection (harness.py),
with the stand-in claude, gh and tailscale of tests/molecule first on its PATH,
the fake GitHub API and a fake Tailscale API on loopback ports, and no systemd:
Remote Control's units and tailscaled's are absent here, so what systemd does
with them is the Molecule scenarios' to show. The release play and the report
are taken from destroy.yml as they stand.

What these guard:
- A released VM is signed out of Claude Code and of gh. No gh token is revoked
  at GitHub: revoking the one this project's device flow made ended every gh
  sign-in of the account (seen live), so the report says where to revoke it and
  what that does; the vault's token, which every VM shares, and a token pasted
  by hand are told apart from it.
- Its Tailscale node is logged out, and with an API credential its device is
  deleted from the tailnet, from this machine: by an API access token, or an
  OAuth client exchanged for one - and only a device named as this project
  asked the VM to join, since the VM reports the ID. A refusal is said with the
  device to delete by hand, and a device already gone as such.
- A VM with no address, or that does not answer, is left as it is, and the
  report names what is left of it to remove by hand.
- The report gives every VM asked for one line, and the line says what truly
  became of it: deleted and released, deleted unreleased and why, released
  but not deleted, or kept by a save that failed, its deletion never tried -
  and the kept SSH alias of a VM still there says which.
- No token, key or secret reaches the output, module arguments included.
- destroy starts a stopped VM to release it or save its sessions, and releases
  before it deletes - finding the address of a VM it started though the cluster
  listing, seconds behind, still says it is stopped.
- Before anything is released, what of a VM's Claude Code sessions is only
  there is saved here, into the test's own directory: never a copy just code
  made still as written, never a link the VM holds or a name it made up, and
  without sudo, which a VM can refuse; a save that fails keeps the VM, neither
  released nor deleted, and destroy_release: false still saves.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from harness import (
    CLAUDE_CALLS,
    GETENT,
    REPO,
    STAND_INS,
    TAILSCALE_CALLS,
    _calls,
    _desktop,
    _every_task,
    _executable,
    _failures,
    _fake_pve,
    _leaked,
    _left_to_do,
    _local_hosts,
    _messages,
    _pve,
    _pve_requests,
    _pve_vars,
    _pve_vm,
    _results,
    _run,
    _run_at_a_terminal,
    _serve,
    _unreachable,
)

DESTROY = REPO / "playbooks" / "destroy.yml"
FAKES = REPO / "tests" / "molecule"
REPORT = "Say what became of each VM, and what is left of it"
ADMIN = "https://login.tailscale.com/admin/machines"

# Canned credentials, each searched for in the whole report.
DEVICE_TOKEN = "gho_device0000000000"
VAULT_TOKEN = "ghp_vault0000000000000000000000000000"
HAND_TOKEN = "gho_pastedbyhand000000000000"
API_TOKEN = "tskey-api-good-0000"
FORBIDDEN_TOKEN = "tskey-api-denied-0000"
CLIENT_SECRET = "tskey-client-good-0000"
REFUSED_CLIENT_SECRET = "tskey-client-refused-0000"
SECRETS = [
    DEVICE_TOKEN,
    VAULT_TOKEN,
    HAND_TOKEN,
    API_TOKEN,
    FORBIDDEN_TOKEN,
    CLIENT_SECRET,
    REFUSED_CLIENT_SECRET,
    "tskey-api-oauth-0000",
]

# One VM per way a release can go. "api" is the Tailscale API credential the
# vault holds, and "method" tailscale_api_auth_method where a case sets it:
# elsewhere the role's default, auto, decides from the credential alone.
CASES = {
    # Everything this project signs a VM in to, and an API access token.
    "everything": {"claude": True, "gh": DEVICE_TOKEN, "device_marker": True, "node": "nEVERYTHING", "api": "token"},
    # The vault's token in gh; Tailscale with no API credential.
    "vault-token": {"gh": VAULT_TOKEN, "vault": True, "node": "nVAULT"},
    # A token pasted by hand; Tailscale through an OAuth client.
    "by-hand": {"gh": HAND_TOKEN, "node": "nBYHAND", "api": "oauth"},
    # A credential Tailscale refuses for this device, named by the method.
    "refused": {"node": "nREFUSED", "api": "forbidden", "method": "token"},
    # An API access token in the vault, and none asked for: it stays unused.
    "kept": {"node": "nKEPT", "api": "token", "method": "none"},
    # An API access token and an OAuth client, one Tailscale would refuse.
    "both": {"node": "nBOTH", "api": "both"},
    # Half an OAuth client: its ID, and no secret.
    "half-oauth": {"node": "nHALF", "api": "half-oauth"},
    # A device the tailnet no longer has.
    "gone": {"node": "nGONE", "api": "token"},
    # A VM reporting another device's ID - a laptop's: never deleted.
    "liar": {"node": "nLAPTOP", "api": "token"},
    # A VM reporting the ID of web-2's device, which it can read in its own tailscale status: web-2 is a VM of the
    # fleet, so web-2 is that VM's name, never Tailscale's renaming of web's.
    "web": {"node": "nWEB2", "api": "token", "others": ["web-2", "db"]},
    # The same with a machine name of the user's own: VM prefixed-2 joins as dev-prefixed-2, made as dev-prefixed is.
    "prefixed": {"node": "nDEVPREFIXED2", "api": "token", "others": ["prefixed-2"], "hostname": "dev-prefixed"},
    # Two VMs whose names make one machine name: nothing tells their devices apart, so neither is deleted.
    "twin-lab": {"node": "nTWIN", "api": "token", "others": ["twin.lab"]},
    # A VM reporting an ID that is a path: it never reaches the API.
    "traversal": {"node": "../../tailnet/-/keys", "api": "token"},
    # The same, as long as the VM likes: said cut, as other text a VM chose is.
    "long-id": {"node": "n" * 150 + "/" + "n" * 300, "api": "token"},
    # A VM whose marker claims the vault token: it is every VM's, never taken for this project's sign-in.
    "marked-vault": {"gh": VAULT_TOKEN, "vault": True, "device_marker": True},
    # Two accounts in gh: with no terminal, its logout will not choose one.
    "two-accounts": {"gh": HAND_TOKEN, "two_accounts": True},
    # Nothing signed in at all.
    "nothing": {},
    # Claude Code sessions of its own, and copies just code made: only the first are saved.
    "sessions": {"sessions": True},
    # The same, with the save turned off: they go with the VM.
    "unsaved": {"sessions": True, "save": False},
    # The same, just code's own directory set elsewhere by vscode_workspaces_dir, its manifest there.
    "workspaces": {"sessions": True, "workspaces": True},
    # No address: stopped and would not start, or an agent that never answered.
    "unreached": {"address": ""},
    # An address, but no answer there: here, no Python where Ansible looks for it.
    "unanswering": {"answers": False},
    # Signed in to Claude, released, and then refused its deletion, as Proxmox refuses a protected VM.
    "stuck": {"claude": True, "undeletable": True},
}

# The VM's sessions in the "sessions" case: begun there, a copy just code wrote
# still as written, a copy continued there, and one with no message.
STARTED = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
COPIED = "bc0e637b-2c3d-4e4f-9a01-23456789abcd"
CONTINUED = "c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
EMPTY = "d4e5f6a7-8b9c-4d0e-9f1a-2b3c4d5e6f70"
VM_FOLDER = "-home-dev-projects-demo"
MAC_FOLDER = "-Users-me-demo"
# A folder no Claude Code names so, and a session in it: the VM's to make up, and never saved.
ODD_FOLDER = "-home-dev-projects-a b"
ODD = "e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a81"
# What a tool printed, as a transcript holds it: it must never reach a run's output.
MARKER = "TRANSCRIPT-MARKER-5e2a9c"


def _transcript(sid: str, cwd: str, conversation: bool = True) -> bytes:
    base = {"cwd": cwd, "sessionId": sid, "version": "2.1.308", "gitBranch": "main"}
    entries = (
        [{**base, "type": "user", "message": {"role": "user", "content": f"run it {MARKER}"}}]
        if conversation
        else [{**base, "type": "summary", "summary": "nothing"}]
    )
    return "".join(json.dumps(entry, separators=(",", ":")) + "\n" for entry in entries).encode()


def _vm_sessions(home: Path, manifest_dir: Path | None = None) -> None:
    """A VM's ~/.claude/projects/ as just code and the VM's own sessions leave it, and just code's manifest.

    A link among them, to a file of this machine's outside the VM's home, and a session in a folder not
    named as Claude Code names one: neither may be saved.
    """
    projects = home / ".claude" / "projects"
    (projects / VM_FOLDER / STARTED / "subagents").mkdir(parents=True)
    (projects / VM_FOLDER / STARTED / "subagents" / "agent-1.jsonl").write_text("{}\n")
    outside = home.parent / f"{home.name}-outside.txt"
    outside.write_text("this machine's\n")
    outside.chmod(0o644)
    (projects / VM_FOLDER / STARTED / "subagents" / "link").symlink_to(outside)
    (projects / ODD_FOLDER).mkdir()
    (projects / ODD_FOLDER / f"{ODD}.jsonl").write_bytes(_transcript(ODD, "/home/dev/projects/a b"))
    (projects / VM_FOLDER / f"{STARTED}.jsonl").write_bytes(_transcript(STARTED, "/home/dev/projects/demo"))
    (projects / VM_FOLDER / f"{EMPTY}.jsonl").write_bytes(_transcript(EMPTY, "/home/dev/projects/demo", False))
    (projects / VM_FOLDER / "memory").mkdir()
    (projects / VM_FOLDER / "memory" / "MEMORY.md").write_text("- a note\n")
    (projects / MAC_FOLDER).mkdir()
    copied = _transcript(COPIED, "/Users/me/demo")
    (projects / MAC_FOLDER / f"{COPIED}.jsonl").write_bytes(copied)
    written = _transcript(CONTINUED, "/Users/me/demo")
    (projects / MAC_FOLDER / f"{CONTINUED}.jsonl").write_bytes(
        written + _transcript(CONTINUED, "/home/dev/projects/demo")
    )
    manifest = (manifest_dir or home / "projects" / ".agent-warehouse") / "claude-sessions" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "format": 1,
                "sessions": {
                    f"{MAC_FOLDER}/{COPIED}.jsonl": hashlib.sha256(copied).hexdigest(),
                    f"{MAC_FOLDER}/{CONTINUED}.jsonl": hashlib.sha256(written).hexdigest(),
                },
            }
        )
    )


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as answer:
        return json.loads(answer.read())


def _hosts(root: Path, cases: dict, github: str, tailscale: str) -> dict:
    tools = {
        "bash": STAND_INS["bash"],
        "getent": GETENT,
        "claude": (FAKES / "fake_claude.sh").read_text(),
        "gh": (FAKES / "fake_gh.sh").read_text(),
        "tailscale": (FAKES / "fake_tailscale.sh").read_text(),
    }
    # No ansible_host: the release play reads each VM's address from claude_vm_release_address, below.
    hosts = _local_hosts(root, cases, tools, address="")
    for name, case in cases.items():
        home = root / name
        (home / ".claude" / "fake").mkdir(parents=True)
        if case.get("claude"):
            (home / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {}}\n')
        if case.get("gh"):
            (home / ".config" / "gh").mkdir(parents=True)
            (home / ".config" / "gh" / "fake-token").write_text(case["gh"] + "\n")
        if case.get("two_accounts"):
            (home / ".config" / "gh" / "fake-two-accounts").write_text("")
        if case.get("device_marker"):
            (home / ".config" / "agent-warehouse").mkdir(parents=True)
            (home / ".config" / "agent-warehouse" / "gh-device-login").write_text(
                hashlib.sha256(case["gh"].encode()).hexdigest() + "\n"
            )
        state = home / ".tailscale-fake"
        state.mkdir()
        if case.get("node"):
            (state / "joined").write_text("")
            (state / "node-id").write_text(case["node"])
        if case.get("sessions"):
            _vm_sessions(home, home / "ws" if case.get("workspaces") else None)
        api = case.get("api", "")
        hosts[name] = {
            **hosts[name],
            "ansible_python_interpreter": str(home / ("python" if case.get("answers", True) else "no-python")),
            "vm_user": "dev",
            **({"destroy_test_undeletable": True} if case.get("undeletable") else {}),
            **({"destroy_test_lost": True} if case.get("lost") else {}),
            **({"destroy_save_sessions": case["save"]} if "save" in case else {}),
            **({"claude_vm_release_others": case["others"]} if "others" in case else {}),
            **({"tailscale_hostname": case["hostname"]} if "hostname" in case else {}),
            **({"vscode_workspaces_dir": str(home / "ws")} if case.get("workspaces") else {}),
            "claude_vm_release_address": case.get("address", "192.0.2.10"),
            "github_projects_home": str(home),
            "github_projects_api_url": github,
            "github_projects_token": VAULT_TOKEN if case.get("vault") else "",
            "tailscale_api_url": tailscale,
            **({"tailscale_api_auth_method": case["method"]} if "method" in case else {}),
            "tailscale_api_token": {"token": API_TOKEN, "both": API_TOKEN, "forbidden": FORBIDDEN_TOKEN}.get(api, ""),
            "tailscale_oauth_client_id": {"oauth": "kOAUTHCLIENT", "both": "kBOTH", "half-oauth": "kHALF"}.get(api, ""),
            "tailscale_oauth_client_secret": {"oauth": CLIENT_SECRET, "both": REFUSED_CLIENT_SECRET}.get(api, ""),
            # Every module's arguments in its result, so that a credential handed
            # to a task without no_log would reach the report searched below.
            "ansible_inject_invocation": True,
        }
    return hosts


# What the stand-in for proxmox_vm says of a VM marked undeletable, as Proxmox refuses a protected one.
REFUSED = "can't remove VM - protection mode enabled"


def _destroy_tail(root: Path, saved: Path | None = None) -> Path:
    """destroy.yml's release play, its deletion and its report: as destroy.yml has them, proxmox_vm aside.

    The deletion is destroy's own play with a stand-in for the role, which deletes nothing and refuses a VM
    with destroy_test_undeletable set, so that its rescue records why. A VM with destroy_test_lost set fails
    a task of its release outside any rescue, as one that stops answering does, and leaves the run. The
    sessions a VM's save brings back go to ``saved``, root/saved without one: never the checkout's .sessions/.
    """
    plays = yaml.safe_load(DESTROY.read_text())
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    # Its task files where they are: the play runs from a copy here.
    release = json.loads(json.dumps(release).replace('"tasks/', f'"{REPO / "playbooks" / "tasks"}/'))
    release["vars"] = {**release.get("vars", {}), "destroy_sessions_dir": str(saved or root / "saved")}
    record = [task.get("name") for task in release["tasks"]].index("Record that the VM was released")
    release["tasks"].insert(
        record,
        {
            "name": "Lose the VM before it is recorded released",
            "ansible.builtin.fail": {"msg": "lost on the way"},
            "when": "destroy_test_lost | default(false) | bool",
        },
    )
    delete = next(play for play in plays if play.get("name") == "Delete the VMs on Proxmox")
    (step,) = [task for task in delete["tasks"] if "block" in task]
    assert step["block"][0]["ansible.builtin.import_role"] == {"name": "proxmox_vm"}
    step["block"][0] = {
        "name": "Stand in for proxmox_vm",
        "ansible.builtin.fail": {"msg": f"{REFUSED}."},
        "when": "destroy_test_undeletable | default(false) | bool",
    }
    holder = next(play for play in plays if any(task.get("name") == REPORT for task in play.get("tasks", [])))
    tasks = holder["tasks"]
    at = next(index for index, task in enumerate(tasks) if task.get("name") == REPORT)
    # The report and its record for NEXT STEPS, then the kept aliases' and
    # theirs: a record's condition naming its report's task var failed every
    # destroy. The aliases themselves are never touched here.
    report = tasks[at : at + 4]
    assert [task.get("name") for task in report] == [
        REPORT,
        "Keep what is left to do for the end of the run",
        "Say which aliases were kept, and why",
        "Keep what is left to do for the end of the run",
    ]
    # With its play's vars, which the report and its records read the VMs from.
    on_these = json.loads(
        json.dumps({"vars": holder.get("vars", {}), "tasks": report}).replace(
            "groups['claude_vm_doomed']", "groups['claude_vms']"
        )
    )
    playbook = root / "destroy_tail.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {**release, "hosts": "claude_vms"},
                {**delete, "hosts": "claude_vms"},
                {"name": "Report", "hosts": "localhost", "gather_facts": False, **on_these},
            ]
        )
    )
    return playbook


@pytest.fixture(scope="module")
def fake_github(tmp_path_factory):
    # Its account and an empty directory of repositories: only the revocation is asked here.
    yield from _serve(FAKES / "fake_github_api.py", "dev", str(tmp_path_factory.mktemp("git")))


# The tailnet's devices, by node ID: each VM's under its own name - by-hand's
# as Tailscale names a taken one - and a laptop that is none of theirs.
DEVICES = {
    "nEVERYTHING": "everything",
    "nVAULT": "vault-token",
    "nBYHAND": "by-hand-1",
    "nREFUSED": "refused",
    "nKEPT": "kept",
    "nBOTH": "both",
    "nHALF": "half-oauth",
    "nLAPTOP": "laptop",
    "nWEB2": "web-2",
    "nDEVPREFIXED2": "dev-prefixed-2",
    "nTWIN": "twin-lab",
}


@pytest.fixture(scope="module")
def fake_tailscale():
    yield from _serve(FAKES / "fake_tailscale_api.py", *(f"{node}={name}" for node, name in DEVICES.items()))


@pytest.fixture(scope="module")
def released(tmp_path_factory, fake_github, fake_tailscale):
    root = tmp_path_factory.mktemp("release")
    hosts = _hosts(root, CASES, fake_github, fake_tailscale)
    # No sudo here. Never a connection: each host's own, local here, as
    # destroy's add_host gives each VM its SSH settings - forced with -e, a
    # connection set in the release play's vars would go unseen, and one went
    # to the VM with the Tailscale API's credential.
    extra = {
        "ansible_become": False,
        # An internal of the tailscale role (vars/main.yml): only -e outranks it.
        "tailscale_settle_seconds": 3,
    }
    report = _run(root, _destroy_tail(root), hosts, "-e", json.dumps(extra), ordered=True)
    return root, report, _get(f"{fake_github}/fake/revoked"), _get(f"{fake_tailscale}/fake/deleted")


def _said(report: dict) -> dict:
    lines = {}
    for name, _host, result in _results(report):
        if name.endswith(REPORT):
            for item in result.get("results", []):
                vm = item.get("claude_release_name")
                if vm:
                    lines[vm] = item["msg"]
    return lines


def test_a_vm_signed_in_to_everything_is_released_from_everything(released):
    root, report, revoked, deleted = released
    assert report["rc"] == 0, _failures(report)
    assert not (root / "everything" / ".claude" / ".credentials.json").exists()
    assert "auth logout" in _calls(root, "everything", *CLAUDE_CALLS)
    assert not (root / "everything" / ".config" / "gh" / "fake-token").exists()
    assert "logout" in _calls(root, "everything", *TAILSCALE_CALLS)
    assert "nEVERYTHING" in deleted
    assert _said(report)["everything"] == (
        "everything: released - Claude Code: signed out; GitHub: gh signed out; the token this project signed it in"
        " with stays valid until revoked at https://github.com/settings/applications, under GitHub CLI - which signs"
        " out every gh you have, so destroy does not; Tailscale: removed from your tailnet."
    )


def test_no_token_is_ever_revoked_at_github(released):
    # Revoking one GitHub CLI token through the credential revocation endpoint
    # ended every gh sign-in of the account - the developer's own included.
    _, _, revoked, _ = released
    assert revoked == []


def test_the_vault_token_and_a_token_pasted_by_hand_are_signed_out_and_told_apart(released):
    root, report, revoked, deleted = released
    for host in ("vault-token", "by-hand"):
        assert not (root / host / ".config" / "gh" / "fake-token").exists(), host
    said = _said(report)
    assert "GitHub: gh signed out (vault_github_token is every VM's, so it is not revoked)" in said["vault-token"]
    assert (
        "GitHub: gh signed out; its token is not one this project recorded making (a sign-in by hand, or one before"
        " this version), and stays valid until revoked - at https://github.com/settings/tokens if it is a personal"
        " access token, or under GitHub CLI at https://github.com/settings/applications, which signs out every gh"
        " you have;"
    ) in said["by-hand"]


def test_a_logout_gh_refuses_is_said_never_reported_as_done(released):
    root, report, revoked, deleted = released
    assert (root / "two-accounts" / ".config" / "gh" / "fake-token").exists()
    assert _said(report)["two-accounts"] == (
        "two-accounts: released - GitHub: gh could not sign out (unable to determine which account to log out of,"
        " please specify `--hostname` and `--user`); its token is not one this project recorded making (a sign-in"
        " by hand, or one before this version), and stays valid until revoked - at https://github.com/settings/tokens"
        " if it is a personal access token, or under GitHub CLI at https://github.com/settings/applications, which"
        " signs out every gh you have."
    )


def test_without_an_api_credential_the_node_is_logged_out_and_its_device_named(released):
    root, report, revoked, deleted = released
    assert "nVAULT" not in deleted
    assert "logout" in _calls(root, "vault-token", *TAILSCALE_CALLS)
    assert _said(report)["vault-token"].endswith(
        f"Tailscale: logged out; its device stays in your tailnet until you delete it at {ADMIN}, or a VM built again"
        " under its name joins as <name>-1."
    )


def test_an_oauth_client_is_exchanged_for_a_token_that_deletes_the_device(released):
    root, report, revoked, deleted = released
    assert "nBYHAND" in deleted
    assert _said(report)["by-hand"].endswith("Tailscale: removed from your tailnet.")


def test_a_refused_deletion_and_a_device_already_gone_are_said(released):
    _, report, _, deleted = released
    said = _said(report)
    assert "nREFUSED" not in deleted
    assert said["refused"] == (
        "refused: released - Tailscale: logged out, but its device was not deleted - Tailscale refused the call"
        " (HTTP 403): the API access token may not delete this device - an OAuth client only those with its tags:"
        f" delete it at {ADMIN}."
    )
    assert said["gone"] == (
        "gone: released - Tailscale: logged out; Tailscale had no such device (HTTP 404) - it was gone already, or is"
        " in a tailnet the API credential does not reach."
    )


def test_a_device_is_deleted_only_under_the_name_this_project_asked_the_vm_to_join_as(released):
    # The ID is the VM's to report, and the credential could delete any device.
    _, report, _, deleted = released
    assert "nLAPTOP" not in deleted
    assert _said(report)["liar"] == (
        "liar: released - Tailscale: logged out, but its device was not deleted - the device under the ID the VM"
        ' reported is named "laptop", not "liar", so it is not this VM\'s; find this VM\'s own device at'
        f" {ADMIN} and delete it."
    )


def test_a_device_named_as_another_vm_of_the_fleet_joins_is_never_deleted(released):
    # by-hand's by-hand-1 is deleted: no VM of the fleet is named so, so it is Tailscale's renaming of a name taken.
    _, report, _, deleted = released
    assert "nWEB2" not in deleted and "nBYHAND" in deleted
    assert _said(report)["web"] == (
        "web: released - Tailscale: logged out, but its device was not deleted - the device under the ID the VM"
        " reported is named \"web-2\", the name another VM of yours joins as, so it may be that VM's; find this VM's"
        f" own device at {ADMIN} and delete it."
    )


def test_the_other_vms_machine_names_are_made_as_this_vm_s_is_made_from_its_name(released):
    # dev-prefixed-2 is prefixed's own name and -2 to the pattern: compared with the bare prefixed-2, it was deleted.
    _, report, _, deleted = released
    assert "nDEVPREFIXED2" not in deleted
    assert _said(report)["prefixed"] == (
        "prefixed: released - Tailscale: logged out, but its device was not deleted - the device under the ID the VM"
        ' reported is named "dev-prefixed-2", the name another VM of yours joins as, so it may be that VM\'s; find'
        f" this VM's own device at {ADMIN} and delete it."
    )


def test_a_device_two_vms_may_join_under_is_never_deleted(released):
    # twin-lab and twin.lab both join as twin-lab, the second as twin-lab-1: the device may be either's.
    _, report, _, deleted = released
    assert "nTWIN" not in deleted
    assert _said(report)["twin-lab"] == (
        "twin-lab: released - Tailscale: logged out, but its device was not deleted - the device under the ID the VM"
        ' reported is named "twin-lab", a name another VM of yours may join as too, so it may be that VM\'s; find'
        f" this VM's own device at {ADMIN} and delete it."
    )


def test_a_node_id_that_is_not_one_tailscale_gives_never_reaches_the_api(released, fake_tailscale):
    # urlencode leaves / alone: in the API's path, ../.. would take the
    # credential to another endpoint.
    _, report, _, _ = released
    assert _said(report)["traversal"] == (
        "traversal: released - Tailscale: logged out, but its device was not deleted - the node ID it reported,"
        ' "../../tailnet/-/keys", is not one Tailscale gives, so Tailscale\'s API was not asked: delete it at'
        f" {ADMIN}."
    )
    asked = _get(f"{fake_tailscale}/fake/asked")
    assert asked and not [path for path in asked if "tailnet" in path or ".." in path or "%2F" in path], asked


def test_a_refused_node_id_is_said_cut_to_200_characters(released):
    _, report, _, _ = released
    said = _said(report)["long-id"]
    assert ('the node ID it reported, "' + "n" * 150 + "/" + "n" * 49 + '", is not one') in said, said
    assert "n" * 50 + '"' not in said


def test_a_credential_in_the_vault_is_enough_and_none_keeps_it_unused(released):
    # everything, by-hand, gone and liar set no method: auto took their credential.
    _, report, _, deleted = released
    assert {"nEVERYTHING", "nBYHAND"} <= set(deleted)
    assert "nKEPT" not in deleted
    assert _said(report)["kept"] == (
        f"kept: released - Tailscale: logged out; its device stays in your tailnet until you delete it at {ADMIN},"
        " or a VM built again under its name joins as <name>-1."
    )


def test_auto_takes_the_api_access_token_over_an_oauth_client(released, fake_tailscale):
    # both's OAuth client has a secret Tailscale refuses: its device goes only by the token.
    _, report, _, deleted = released
    assert "nBOTH" in deleted
    assert _said(report)["both"] == "both: released - Tailscale: removed from your tailnet."
    assert "kBOTH" not in _get(f"{fake_tailscale}/fake/exchanged")


def test_half_an_oauth_client_is_said_and_never_sent(released, fake_tailscale):
    _, report, _, deleted = released
    assert "nHALF" not in deleted
    assert _said(report)["half-oauth"] == (
        "half-oauth: released - Tailscale: logged out, but its device was not deleted - the OAuth client needs both"
        f" tailscale_oauth_client_id and tailscale_oauth_client_secret: delete it at {ADMIN}."
    )
    assert _get(f"{fake_tailscale}/fake/exchanged") == ["kOAUTHCLIENT"]


def test_the_vault_token_is_never_taken_for_this_project_s_sign_in_whatever_the_vm_s_marker_says(released):
    _, report, _, _ = released
    assert "GitHub: gh signed out (vault_github_token is every VM's" in _said(report)["marked-vault"]


def test_a_vm_with_nothing_signed_in_is_said_to_have_nothing_to_end(released):
    _, report, _, _ = released
    assert _said(report)["nothing"] == "nothing: released - it had nothing signed in to end."


def _saved(root: Path, vm: str) -> Path:
    (folder,) = (root / "saved").glob(f"{vm}-*")
    return folder


def test_what_only_the_vm_has_of_its_sessions_is_saved_as_it_was(released):
    # Begun on the VM, and a copy continued there, each with its folder, and
    # the auto memory: not a copy still as just code wrote it, which this
    # machine has, nor a transcript without a message, which resumes nothing.
    root, report, _, _ = released
    saved = _saved(root, "sessions")
    assert saved.name.removeprefix("sessions-").replace("-", "").isdigit()
    here = sorted(str(path.relative_to(saved)) for path in saved.rglob("*") if path.is_file())
    assert here == [
        f"projects/{MAC_FOLDER}/{CONTINUED}.jsonl",
        f"projects/{VM_FOLDER}/{STARTED}.jsonl",
        f"projects/{VM_FOLDER}/{STARTED}/subagents/agent-1.jsonl",
        f"projects/{VM_FOLDER}/memory/MEMORY.md",
        "sessions.json",
    ]
    vm = root / "sessions" / ".claude" / "projects"
    for name in here[:4]:
        assert (saved / name).read_bytes() == (vm / name.removeprefix("projects/")).read_bytes(), name
    # This machine's user's alone.
    for path in [saved, *saved.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600), path
    listed = json.loads((saved / "sessions.json").read_text())
    assert listed["vm"] == "sessions"
    # The Proxmox host the VM was on (claude_ssh_origin): none in this inventory, which has no proxmox group.
    assert "host" in listed and listed["host"] is None
    assert [(item["id"], item["started_on_the_vm"], item["cwd"]) for item in listed["sessions"]] == [
        (CONTINUED, False, "/Users/me/demo"),
        (STARTED, True, "/home/dev/projects/demo"),
    ]
    started = listed["sessions"][1]
    assert started["transcript"] == str(saved / "projects" / VM_FOLDER / f"{STARTED}.jsonl")
    assert started["resume"] == f"claude --resume {started['transcript']}"
    assert listed["memory"] == [str(saved / "projects" / VM_FOLDER / "memory")]
    assert _said(report)["sessions"] == (
        "sessions: released - Claude Code sessions: 2 saved (1 started there, 1 continued there), with the auto"
        f" memory of 1 project, in {saved} - its sessions.json has the `claude --resume <path>` that continues each"
        " here, with Claude Code 2.1.308 or newer, and what they hold is the VM's writing; left out 2 entries of the"
        " VM that were a link, not a plain file or folder, or not named as Claude Code names its own."
    )
    # What it is, and what resumes it.
    assert listed["note"].startswith("Written on sessions and saved as it sent them, plain files only")
    assert listed["newest_version"] == "2.1.308" and {item["version"] for item in listed["sessions"]} == {"2.1.308"}


def test_a_link_the_vm_holds_never_reaches_or_changes_anything_here(released):
    # rsync brought a VM's link over as a link, and the chmod after it followed it out of the folder.
    root, _, _, _ = released
    saved = _saved(root, "sessions")
    assert not any(path.is_symlink() for path in saved.rglob("*"))
    outside = root / "sessions-outside.txt"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644 and outside.read_text() == "this machine's\n"
    assert not any(ODD in str(path) for path in saved.rglob("*"))


def test_the_manifest_is_read_where_vscode_workspaces_dir_puts_just_code_s_directory(released):
    # Read from the projects directory instead, every copy just code made would be saved as continued.
    root, report, _, _ = released
    saved = _saved(root, "workspaces")
    assert sorted(path.name for path in (saved / "projects" / MAC_FOLDER).iterdir()) == [f"{CONTINUED}.jsonl"]
    assert "2 saved (1 started there, 1 continued there)" in _said(report)["workspaces"]


def test_a_vm_with_none_of_its_own_saves_nothing_and_says_nothing_of_it(released):
    root, report, _, _ = released
    assert sorted(path.name.rsplit("-", 2)[0] for path in (root / "saved").iterdir()) == ["sessions", "workspaces"]
    assert "Claude Code sessions" not in _said(report)["nothing"]


def test_with_the_save_off_the_sessions_go_with_the_vm_and_it_is_said(released):
    root, report, _, _ = released
    assert not list((root / "saved").glob("unsaved-*"))
    assert _said(report)["unsaved"] == (
        "unsaved: released - Claude Code sessions: not saved (destroy_save_sessions: false)."
    )


def test_no_transcript_reaches_the_output(released):
    _, report, _, _ = released
    assert _leaked(report, [MARKER]) == []


# What a VM deleted unreleased leaves to remove by hand, after the reason it was not released.
LEFT = (
    f"its Tailscale device, if it was on your tailnet, stays until you delete it at {ADMIN} (or a VM built again"
    " under its name joins as <name>-1); a GitHub token its sign-in made stays valid until revoked at"
    " https://github.com/settings/applications, under GitHub CLI (which signs out every gh); and Anthropic has no"
    " way to revoke its Claude Code login from outside. Claude Code sessions: not saved, nor its auto memory, if it"
    " had any."
)


def test_a_vm_with_no_address_is_left_as_it_is_and_what_is_left_named(released):
    root, report, _, _ = released
    assert _calls(root, "unreached", *TAILSCALE_CALLS) == []
    assert _said(report)["unreached"] == f"unreached: had no address to reach it at, so it was not released: {LEFT}"


def test_a_vm_that_does_not_answer_is_left_as_it_is_and_what_is_left_named(released):
    # It has an address; Ansible finds nothing to run there. Deleted as it is, as one whose SSH fails.
    root, report, _, _ = released
    assert _calls(root, "unanswering", *TAILSCALE_CALLS) == []
    assert "auth logout" not in _calls(root, "unanswering", *CLAUDE_CALLS)
    assert _said(report)["unanswering"] == f"unanswering: could not be reached, so it was not released: {LEFT}"


def test_a_vm_released_and_then_refused_its_deletion_says_both_and_keeps_its_line_for_next_steps(released):
    # Its line said nothing before: only the VMs deleted had one.
    root, report, _, _ = released
    assert not (root / "stuck" / ".claude" / ".credentials.json").exists()
    said = _said(report)["stuck"]
    assert said == (
        f"stuck: not deleted - its deletion failed (Stand in for proxmox_vm: {REFUSED}), so it is still there, its"
        " SSH alias with it: run just destroy stuck again once that is fixed. It was released - Claude Code: signed"
        " out."
    )
    assert _left_to_do(report)["stuck"] == {"destroy": said}


def test_every_vm_asked_for_gets_exactly_one_line(released):
    # Those deleted and those not, never one twice: the report loops over every VM of the run.
    _, report, _, _ = released
    (lines,) = [result["results"] for name, _, result in _results(report) if name.endswith(REPORT)]
    assert sorted(item["claude_release_name"] for item in lines) == sorted(CASES)


def test_next_steps_keep_each_vm_with_something_left_to_do_and_nothing_for_one_released_clean(released):
    # What the run's last lines list (callback_plugins/agent_warehouse.py):
    # the report's own line where a GitHub token stays valid, a Tailscale
    # device stays in the tailnet, or nothing was released at all.
    _, report, _, _ = released
    # The run itself ended well: a record failing after its report fails the destroy.
    assert report["rc"] == 0, _failures(report)
    said = _said(report)
    left = {vm: matters["destroy"] for vm, matters in _left_to_do(report).items()}
    # The alias of the one VM still there is named in that VM's own line, kept with it, and not again: the
    # aliases' line, which names the VM and its rerun too, is recorded empty.
    assert set(left) == set(CASES) | {"kept SSH aliases"}
    assert left["kept SSH aliases"] == ""
    assert _messages(report, "Say which aliases were kept, and why")["localhost"] == (
        "Kept the SSH alias of stuck: its deletion failed above, so the VM is still there. `just destroy stuck` again"
        " once the cause is fixed removes it with the VM."
    )
    for vm in (
        "everything",
        "vault-token",
        "by-hand",
        "refused",
        "kept",
        "half-oauth",
        "liar",
        "two-accounts",
        "unreached",
        "unanswering",
        "stuck",
    ):
        assert left[vm] == said[vm], vm
    for vm in ("gone", "both", "marked-vault", "nothing", "sessions", "unsaved", "workspaces"):
        assert left[vm] == "", (vm, said[vm])


def test_no_token_key_or_secret_reaches_the_output(released):
    _, report, _, _ = released
    assert _leaked(report, SECRETS) == []


@pytest.fixture(scope="module")
def kept(tmp_path_factory, fake_github, fake_tailscale):
    """A destroy whose save fails, its folder here not writable: the VM signed in to Claude, with a session.

    Beside it, with nothing to save: stuck, released and then refused its deletion; guarded, which does not
    answer and is refused its deletion too, as the provision scenario's protected VM; and lost, which the run
    loses part way through its release, signed out of Claude Code by then.
    """
    root = tmp_path_factory.mktemp("kept")
    cases = {
        "kept": {"claude": True, "sessions": True},
        "guarded": {"answers": False, "undeletable": True},
        "stuck": {"claude": True, "undeletable": True},
        "lost": {"claude": True, "lost": True},
    }
    hosts = _hosts(root, cases, fake_github, fake_tailscale)
    locked = root / "locked"
    locked.mkdir(mode=0o500)
    try:
        report = _run(root, _destroy_tail(root, locked / "saved"), hosts, "-e", json.dumps({"ansible_become": False}))
    finally:
        locked.chmod(0o700)
    return root, report


def test_a_vm_whose_sessions_could_not_be_saved_is_neither_released_nor_deleted(kept):
    root, report = kept
    assert (root / "kept" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "kept", *CLAUDE_CALLS)
    (why,) = [
        result["ansible_facts"]["claude_vm_delete_failure"]
        for name, host, result in _results(report)
        if name == "Keep the VM, whose sessions could not be saved"
    ]
    assert why.startswith(
        "kept was neither released nor deleted: its Claude Code sessions could not be saved (session_save | Bring them"
        " here: The sessions could not be brought here: "
    )
    assert why.endswith(
        "Run just destroy again once that is fixed, or set destroy_save_sessions: false to delete it without them."
    )
    assert MARKER not in json.dumps(report)


def test_a_vm_kept_by_its_save_is_said_to_be_kept_for_that_and_never_for_a_deletion_that_failed(kept):
    # Its deletion was never tried: the kept alias once said it had failed.
    _, report = kept
    (error,) = [
        result["ansible_facts"]["claude_vm_save_error"]
        for name, host, result in _results(report)
        if name == "Keep the VM, whose sessions could not be saved"
    ]
    assert error.startswith("session_save | Bring them here: The sessions could not be brought here: ")
    said = _said(report)
    assert said["kept"] == (
        f"kept: kept, its SSH alias with it, neither released nor deleted - its Claude Code sessions could not be"
        f" saved ({error}). Run just destroy kept again once that is fixed, or set destroy_save_sessions: false to"
        " delete it without them."
    )
    assert said["stuck"] == (
        f"stuck: not deleted - its deletion failed (Stand in for proxmox_vm: {REFUSED}), so it is still there, its"
        " SSH alias with it: run just destroy stuck again once that is fixed. It was released - Claude Code: signed"
        " out."
    )
    assert said["guarded"] == (
        f"guarded: not deleted - its deletion failed (Stand in for proxmox_vm: {REFUSED}), so it is still there, its"
        " SSH alias with it: run just destroy guarded again once that is fixed. It was not released: it could not be"
        " reached."
    )
    aliases = _messages(report, "Say which aliases were kept, and why")["localhost"]
    assert aliases == (
        "Kept the SSH aliases of guarded, stuck: their deletion failed above, so the VMs are still there; and of kept:"
        " its Claude Code sessions could not be saved, so the VM was neither released nor deleted, and is still"
        " there; and of lost: the run lost it before its deletion, so the VM is still there. `just destroy"
        " guarded,kept,lost,stuck` again once the cause is fixed removes each with its VM."
    )
    # Each VM still there once in NEXT STEPS, by its own line, which names its alias: never again by the aliases'.
    assert {vm: matters["destroy"] for vm, matters in _left_to_do(report).items()} == {
        "guarded": said["guarded"],
        "kept": said["kept"],
        "lost": said["lost"],
        "stuck": said["stuck"],
        "kept SSH aliases": "",
    }


def test_a_vm_the_run_lost_part_way_through_its_release_is_said_not_deleted_with_how_far_it_went(kept):
    # Never deleted, nor said to have failed its deletion: it left the run before the delete play.
    root, report = kept
    assert not (root / "lost" / ".claude" / ".credentials.json").exists()
    assert _said(report)["lost"] == (
        "lost: not deleted - the run lost it before its deletion (a task failed on it above, or it stopped"
        " answering), so it is still there, its SSH alias with it: run just destroy lost again once that is fixed."
        " Released so far - Claude Code: signed out."
    )


# What ssh says of a VM reached through the hop: the connection ended before its sshd spoke (route.hop_unanswered),
# a VM that takes each connection and closes it without a word, and a VM's own sshd refusing the login.
CLOSED = "Connection closed by UNKNOWN port 65535"
WORDLESS = f"kex_exchange_identification: Connection closed by remote host\n{CLOSED}"
DENIED = "dev@192.0.2.85: Permission denied (publickey)."

# Through the Proxmox host, VMs the run's connection did not reach, each telling the hop's own forward something else
# (lib/claude_vm/route.py's forward_verdict), and what ssh says of each: stale answers it, and the run again once the
# shared connection was stopped and made again; still answers it and closes each of the run's connections without a
# word, before its sshd speaks; forbidden is refused by the host; down refuses the host's connection itself; denied
# answers it, and its own sshd refuses the run's login; stalled never got a forward, the login to the host for it
# never finishing. And nopy, whose connection works and whose module fails: no Python where Ansible looks.
THROUGH = {
    "stale": ("192.0.2.81", "answer", CLOSED),
    "still": ("192.0.2.82", "answer", WORDLESS),
    "forbidden": ("192.0.2.83", "prohibited", CLOSED),
    "down": ("192.0.2.84", "closed", CLOSED),
    "denied": ("192.0.2.85", "answer", DENIED),
    "stalled": ("192.0.2.86", "stalled", CLOSED),
}


def _ssh_saying(hostvars: dict, root: Path, name: str, said: str) -> None:
    """A host of _local_hosts reached over ssh whose every connection fails as ssh says ``said``."""
    _unreachable(hostvars, root)
    program = root / "bin" / f"ssh-{name}"
    program.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(said)} >&2\nexit 255\n")
    program.chmod(0o755)
    hostvars.update(ansible_ssh_executable=str(program), ansible_scp_executable=str(program))
    hostvars.update(ansible_sftp_executable=str(program))


@pytest.fixture(scope="module")
def through(tmp_path_factory, fake_github, fake_tailscale):
    """destroy's release, deletion and report for THROUGH, through a stand-in hop (test_route.py's)."""
    import shutil
    import tempfile

    from test_route import STAND_IN

    from claude_vm import route

    root = tmp_path_factory.mktemp("through")
    hop = root / "hop"
    hop.mkdir()
    ssh = hop / "ssh"
    ssh.write_text(STAND_IN)
    ssh.chmod(0o755)
    (hop / "mode").write_text("open")
    cases = {name: {"sessions": True} for name in THROUGH} | {"nopy": {"sessions": True, "answers": False}}
    hosts = _hosts(root, cases, fake_github, fake_tailscale)
    for name, (address, forward, said) in THROUGH.items():
        (hop / f"forward-{address}").write_text(forward)
        _ssh_saying(hosts[name], root, name, said)
        hosts[name].update(
            ansible_host=address,
            claude_vm_release_address=address,
            claude_vm_ssh_via="-J root@100.64.0.7",
        )
    hosts["nopy"].update(claude_vm_release_address="192.0.2.87", claude_vm_ssh_via="-J root@100.64.0.7")
    # stale answers once the run's connection was stopped and made again: here, as a local connection.
    hosts["stale"]["ansible_connection"] = (
        "{{ 'local' if lookup('ansible.builtin.file', '" + str(hop / "calls") + "', errors='ignore')"
        " is search(' -O stop ') else 'ssh' }}"
    )
    control = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    try:
        settled = route.hop_settings(ssh=str(ssh), user="root", host="100.64.0.7", control_dir=control, name="pve")
        extra = {
            "ansible_become": False,
            "claude_vm_route": {"route": "host", "setting": "host", "hop": settled, "policy": "accept-new"},
        }
        report = _run(root, _destroy_tail(root), hosts, "-e", json.dumps(extra), ordered=True)
    finally:
        shutil.rmtree(control, ignore_errors=True)
    return root, hop, report


# Every line of a VM kept names a way out that works: both switches delete it as it is.
WAY_OUT = (
    " again once it answers, or set destroy_release: false and destroy_save_sessions: false, which delete it as it is,"
    " neither released nor saved."
)


def test_through_the_host_a_vm_is_kept_only_where_the_road_is_still_what_fails(through):
    root, hop, report = through
    said = _said(report)
    # Answered the hop, then the run once its connection was made again: released, saved and deleted as any VM.
    assert said["stale"].startswith("stale: released - ")
    assert (_saved(root, "stale") / "projects" / VM_FOLDER / f"{STARTED}.jsonl").exists()
    # Answered the hop and closed the run's connection again before its sshd spoke: kept, and how to delete it anyway.
    assert said["still"] == (
        "still: kept, its SSH alias with it, neither released nor deleted - it answered a forward of its own through"
        " pve (root@100.64.0.7), but not this run's connection, made again. `just status still` says what the host"
        f" reports for it; run just destroy still{WAY_OUT}"
    )
    # The hop at fault: refused to forward, or its login for the forward never finished.
    assert said["forbidden"].startswith(
        "forbidden: kept, its SSH alias with it, neither released nor deleted - pve (root@100.64.0.7) refused to"
        " forward to forbidden at 192.0.2.83: "
    )
    assert said["stalled"].startswith(
        "stalled: kept, its SSH alias with it, neither released nor deleted - the login to pve (root@100.64.0.7) for a"
        " forward of its own to stalled at 192.0.2.86 did not finish within 15 s, so forwarding there was not checked"
    )
    assert all(said[name].endswith(WAY_OUT) for name in ("still", "forbidden", "stalled"))
    # Its own: deleted unsaved, as under direct, and never sent to just status, which cannot read a VM that is gone.
    assert said["down"].startswith("down: could not be reached, so it was not released: ")
    assert said["down"].endswith(
        "It did not answer through pve (root@100.64.0.7): the host's connection to it was refused (Connection refused)."
    )
    assert said["denied"].startswith("denied: could not be reached, so it was not released: ")
    assert said["denied"].endswith(
        "It answered a forward of its own through pve (root@100.64.0.7), so the fault is the VM's own: this run's"
        " connection to it, made again, reached it and failed there, as it would under vm_ssh_route: direct."
    )
    # Its connection worked: the host is not asked, and nothing of the hop is said.
    assert said["nopy"].startswith("nopy: could not be reached, so it was not released: ")
    assert "pve" not in said["nopy"]
    assert all("just status" not in said[name] for name in ("down", "denied", "nopy"))
    gone = [
        host
        for name, host, result in _results(report)
        if name == "Record that the VM was deleted" and not result.get("skipped")
    ]
    assert sorted(gone) == ["denied", "down", "nopy", "stale"]
    # What the last play fails the run with (the tail here stops before it).
    kept = {
        host: result["ansible_facts"]
        for name, host, result in _results(report)
        if name == "Keep a VM the road through the Proxmox host is still what fails for" and not result.get("skipped")
    }
    assert sorted(kept) == ["forbidden", "stalled", "still"]
    failure = kept["still"]["claude_vm_delete_failure"]
    assert failure.startswith("still was neither released nor deleted: it answered a forward of its own")
    assert "`just status still` says what the host reports for it" in failure
    assert failure.endswith("which delete it as it is, neither released nor saved.")
    assert {name: facts["claude_vm_release_hop_fault"] for name, facts in kept.items()} == {
        "forbidden": True,
        "stalled": True,
        "still": False,
    }
    # The aliases: the host at fault only where it was.
    (aliases,) = [
        result["msg"]
        for name, _, result in _results(report)
        if name == "Say which aliases were kept, and why" and "msg" in result
    ]
    assert "forbidden, stalled: they did not answer through the Proxmox host, which was at fault" in aliases, aliases
    assert "still: it answered a forward of its own through the Proxmox host but not this run's connection" in aliases
    # The hop: made sure of, one forward of its own to each VM that was unreachable, at VERBOSE, one stop and the
    # connection made again; never -O exit, and nothing for nopy.
    calls = (hop / "calls").read_text()
    assert " -O exit " not in calls and calls.count(" -O stop ") == 1
    asked = sorted(re.findall(r"-W (192\.0\.2\.8\d):22", calls))
    assert asked == sorted(address for address, _, _ in THROUGH.values()) and "192.0.2.87" not in calls
    own = [line for line in calls.splitlines() if " -W " in line]
    assert all("ControlPath=none" in line and "LogLevel=VERBOSE" in line for line in own)


# The whole of destroy.yml through a stand-in hop (test_route.py's), against the fake Proxmox: a DELETE is a VM
# deleted. Each VM's ssh is a stand-in that runs the first ProxyCommand of its line as ssh runs one - %p its port,
# nothing on its standard input, ended after 4 s - and a proxy that fails is a connection closed; then, by the VM's
# mode, fails as ssh fails or runs the command here, as the VM: its modules run with the harness's stand-in Python, its
# HOME a directory of the test's own. -G is the real ssh's: the route's check asks a VM's own ssh which proxy it takes.
VM_SSH = r"""#!/bin/bash
self=$(readlink -f "$0"); here=$(dirname "$self")
for word in "$@"; do [ "$word" = -G ] && exec /usr/bin/ssh "$@"; done
args=("$@"); n=${#args[@]}; i=0; dest=; proxy=; port=22
while [ "$i" -lt "$n" ]; do
  a=${args[$i]}
  case $a in
    -o) v=${args[$((i + 1))]}
        case $v in ProxyCommand=*) [ -z "$proxy" ] && proxy=${v#ProxyCommand=} ;; Port=*) port=${v#Port=} ;; esac
        i=$((i + 2)) ;;
    -p) port=${args[$((i + 1))]}; i=$((i + 2)) ;;
    -[JFilEeLRDWSbcmOQwBI]) i=$((i + 2)) ;;
    -*) i=$((i + 1)) ;;
    *) dest=$a; i=$((i + 1)); break ;;
  esac
done
host=${dest##*@}
printf '%s %s\n' "$dest" "$*" >> "$here/vm-ssh.log"
if [ -n "$proxy" ] && [ "$proxy" != none ]; then
  timeout 4 /bin/sh -c "${proxy//%p/$port}" < /dev/null > /dev/null 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    [ "$rc" -eq 124 ] && echo "Connection timed out during banner exchange" >&2
    echo "Connection closed by UNKNOWN port 65535" >&2
    exit 255
  fi
fi
case $(cat "$here/vm-$host" 2>/dev/null || echo ok) in
  ok) ;;
  denied) echo "$dest: Permission denied (publickey,password)." >&2; exit 255 ;;
  hostkey) echo "Host key verification failed." >&2; exit 255 ;;
  wordless) echo "kex_exchange_identification: Connection closed by remote host" >&2
    echo "Connection closed by UNKNOWN port 65535" >&2; exit 255 ;;
esac
[ "$i" -lt "$n" ] || exit 0
exec /bin/bash -c "${args[*]:$i}"
"""


def _address(vmid: int) -> str:
    """The address the fake Proxmox's guest agent reports for a VM."""
    return f"192.0.2.{vmid % 200 + 50}"


def _destroy_through(at: dict, vms: dict, mode: str = "open", connection: str = "", **extra) -> tuple:
    """destroy.yml for ``vms``, by name ``{name: {vmid, ssh, forward, python, sessions, running}}``, through the hop at
    ``at`` (through_whole's): the hop's mode, the shared connection's state ("quiet": it says it runs and carries
    nothing) and extra vars. (the report, the hop's calls, the VMs' ssh calls, the names of the VMs deleted)."""
    from claude_vm import route

    tmp_path, base, root, hop, vm_ssh = at["tmp"], at["base"], at["root"], at["hop"], at["vm_ssh"]
    (hop / "mode").write_text(mode)
    cases = {
        name: {"sessions": vm.get("sessions", True), "answers": vm.get("python", True)} for name, vm in vms.items()
    }
    hosts = _hosts(tmp_path / "vms", cases, at["github"], at["tailscale"])
    vmids = {}
    for name, vm in vms.items():
        _pve_vm(base, vm["vmid"], name, running=vm.get("running", True))
        vmids[str(vm["vmid"])] = name
        (vm_ssh / f"vm-{_address(vm['vmid'])}").write_text(vm.get("ssh", "ok"))
        if vm.get("forward"):
            (hop / f"forward-{_address(vm['vmid'])}").write_text(vm["forward"])
        # What outranks the publisher's add_host is only its Python, the harness's wrapper, whose HOME is the VM's own
        # directory: never a connection or an address of its own.
        kept = {
            key: value
            for key, value in hosts[name].items()
            if key not in ("ansible_connection", "claude_vm_release_address", "ansible_host")
        }
        (root / "host_vars" / f"{name}.yml").write_text(yaml.safe_dump(kept))
    if connection:
        settled = route.hop_settings(ssh=str(hop / "ssh"), user="root", host="100.64.0.7", control_dir=at["control"])
        Path(settled["socket"]).write_text(connection)
    extra = {
        "vm_name": ",".join(vms),
        "confirm_destroy": "yes",
        "destroy_sessions_dir": str(tmp_path / "saved"),
        "claude_vm_release_agent_delay": 0.2,
        "ansible_become": False,
        "tailscale_settle_seconds": 3,
        # proxmox_vm validates a VM's login before it deletes one: a key of the test's, never ~/.ssh's.
        "vm_ssh_public_keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse test"],
        **extra,
    }
    env = {"PATH": f"{vm_ssh}:{_desktop(root)}:{os.environ['PATH']}"}
    args = ["-i", str(root / "proxmox.yml"), "-e", json.dumps(extra)]
    report = _run(root, DESTROY, {}, *args, timeout=300, env=env)
    deleted = sorted(
        vmids.get(one["path"].rsplit("/", 1)[-1], one["path"])
        for one in _pve_requests(tmp_path)
        if one["method"] == "DELETE"
    )
    calls = (hop / "calls").read_text() if (hop / "calls").exists() else ""
    vm_calls = (vm_ssh / "vm-ssh.log").read_text() if (vm_ssh / "vm-ssh.log").exists() else ""
    return report, calls, vm_calls, deleted


@pytest.fixture
def through_whole(tmp_path, fake_github, fake_tailscale):
    """destroy.yml against the fake Proxmox, the route through a stand-in hop: _destroy_through, ready to run."""
    import functools
    import tempfile

    from test_route import STAND_IN

    control = tempfile.mkdtemp(prefix="aw-", dir="/tmp")
    try:
        for base in _fake_pve(tmp_path):
            _pve(base, "PUT", "fake/agent-ready-after", polls=0)
            root = tmp_path / "run"
            _pve_vars(root, base, {"vm_ssh_route": "host"})
            hop, vm_ssh = tmp_path / "hop", tmp_path / "vm-ssh"
            hop.mkdir()
            vm_ssh.mkdir()
            _executable(hop / "ssh", STAND_IN)
            _executable(vm_ssh / "ssh", VM_SSH)
            (hop / "forward").write_text("answer")
            given = {
                "ansible_ssh_executable": str(vm_ssh / "ssh"),
                "ansible_ssh_transfer_method": "piped",
                "ansible_control_path_dir": control,
            }
            (root / "group_vars" / "all" / "vm_ssh.yml").write_text(yaml.safe_dump(given))
            pve = {
                "ansible_host": "100.64.0.7",
                "ansible_user": "root",
                "ansible_ssh_executable": str(hop / "ssh"),
                "ansible_control_path_dir": control,
            }
            (root / "proxmox.yml").write_text(yaml.safe_dump({"proxmox": {"hosts": {"pve": pve}}}))
            (root / "host_vars").mkdir()
            at = {
                "tmp": tmp_path,
                "base": base,
                "root": root,
                "hop": hop,
                "vm_ssh": vm_ssh,
                "control": control,
                "github": fake_github,
                "tailscale": fake_tailscale,
            }
            yield functools.partial(_destroy_through, at)
    finally:
        shutil.rmtree(control, ignore_errors=True)


def test_a_login_to_the_hop_that_stalls_keeps_the_vm_and_a_connection_not_made_again_keeps_it(through_whole):
    # The shared connection says it runs and carries nothing, and each VM is healthy: alpha's forward of its own is a
    # login to the host that never finishes (a key that waits for a touch), never read as alpha saying nothing; beta
    # answers it, and the connection cannot be made again (the host now refuses the login). Neither is deleted.
    vms = {"alpha": {"vmid": 9020, "forward": "stalled"}, "beta": {"vmid": 9021}}
    report, calls, _, deleted = through_whole(vms, mode="refused", connection="quiet")
    assert report["rc"] == 2, _failures(report)
    assert deleted == []
    said = _said(report)
    assert said["alpha"].startswith(
        "alpha: kept, its SSH alias with it, neither released nor deleted - the login to pve (root@100.64.0.7) for a"
        " forward of its own to alpha at 192.0.2.70 did not finish within 15 s"
    )
    assert said["beta"].startswith(
        "beta: kept, its SSH alias with it, neither released nor deleted - it answered a forward of its own through pve"
        " (root@100.64.0.7), and the connection through the host could not be made again: root@pve: Permission denied"
    )
    assert all(said[name].endswith(WAY_OUT) for name in ("alpha", "beta"))
    # The start's forward to alpha checked nothing, and said so.
    assert any(
        "did not finish within 15 s, so forwarding there was not checked" in message
        for message in _messages(report, "vm_route | Say a VM that did not answer through the host").values()
    )
    assert calls.count(" -O stop ") == 1 and " -O exit " not in calls


def test_a_stale_connection_in_front_of_a_vm_that_refuses_the_login_ends_with_the_vm_deleted(through_whole):
    # Both first reaches end at the stale connection; both VMs answer a forward of their own, and the connection is
    # made again: good is released, saved and deleted; denied's own sshd then refuses the login, and it goes unsaved.
    vms = {"denied": {"vmid": 9020, "ssh": "denied"}, "good": {"vmid": 9021}}
    report, calls, _, deleted = through_whole(vms, connection="quiet")
    assert report["rc"] == 0, _failures(report)
    assert deleted == ["denied", "good"]
    said = _said(report)
    assert said["good"].startswith("good: released - ")
    assert said["denied"].startswith("denied: could not be reached, so it was not released: ")
    assert "so the fault is the VM's own" in said["denied"]
    assert calls.count(" -O stop ") == 1


def test_a_vm_at_fault_itself_through_the_host_goes_as_under_direct_and_one_that_closes_each_connection_is_kept(
    through_whole,
):
    # The shared connection works. denied, hostkey and wordless are unreachable, each answers a forward of its own,
    # and the connection is made again: denied's and hostkey's sshd spoke, their own fault, and they go, said
    # unsaved; wordless closes each of the run's connections before its sshd speaks, and is kept. nopy's connection
    # works and its module fails: never asked about, and it goes as under direct.
    vms = {
        "denied": {"vmid": 9021, "ssh": "denied"},
        "hostkey": {"vmid": 9022, "ssh": "hostkey"},
        "nopy": {"vmid": 9023, "python": False},
        "wordless": {"vmid": 9024, "ssh": "wordless"},
    }
    report, calls, _, deleted = through_whole(vms)
    assert report["rc"] == 2, _failures(report)
    assert deleted == ["denied", "hostkey", "nopy"]
    said = _said(report)
    for name in ("denied", "hostkey", "nopy"):
        assert said[name].startswith(f"{name}: could not be reached, so it was not released: "), said[name]
        assert "Claude Code sessions: not saved" in said[name]
    assert said["wordless"].endswith(WAY_OUT)
    # Forwards of their own: the start's, to the first VM by name, then one to each VM that was unreachable.
    own = [line for line in calls.splitlines() if " -W " in line and "ControlPath=none" in line]
    assert sorted(re.search(r" -W (\S+):22 ", line).group(1) for line in own) == [
        _address(9021),
        _address(9021),
        _address(9022),
        _address(9024),
    ]


def test_a_vm_whose_module_fails_through_the_host_stops_no_connection(through_whole):
    report, calls, _, deleted = through_whole({"nopy": {"vmid": 9023, "python": False}})
    assert report["rc"] == 0, _failures(report)
    assert deleted == ["nopy"]
    # The start's forward to it, and nothing after: its connection worked.
    own = [line for line in calls.splitlines() if " -W " in line and "ControlPath=none" in line]
    assert len(own) == 1 and " -O stop " not in calls


@pytest.mark.parametrize("forward", ["answer", "prohibited"])
def test_destroy_reads_the_jump_a_vm_s_own_group_gives_it(through_whole, tmp_path, forward):
    # Set where the run's refusal says to, on the claude_vms group: the VM is reached by it, and the host is asked
    # nothing, whether or not it would forward.
    group = tmp_path / "run" / "group_vars" / "claude_vms.yml"
    group.write_text("ansible_ssh_common_args: -o ProxyJump=jump.invalid\n")
    (tmp_path / "hop" / "forward").write_text(forward)
    report, calls, vm_calls, deleted = through_whole({"alpha": {"vmid": 9020}})
    assert report["rc"] == 0, _failures(report)
    assert deleted == ["alpha"] and _said(report)["alpha"].startswith("alpha: released - ")
    assert " -W " not in calls
    lines = [line for line in vm_calls.splitlines() if line.startswith(f"dev@{_address(9020)} ")]
    assert lines and all("ProxyJump=jump.invalid" in line and "ProxyCommand=" not in line for line in lines)


@pytest.fixture(scope="module")
def overfull(tmp_path_factory, fake_github, fake_tailscale):
    """A VM with sessions of its own, more of them than destroy_sessions_max_size lets a save hold."""
    root = tmp_path_factory.mktemp("overfull")
    hosts = _hosts(root, {"overfull": {"claude": True, "sessions": True}}, fake_github, fake_tailscale)
    extra = {"ansible_become": False, "destroy_sessions_max_size": "0.1K"}
    return root, _run(root, _destroy_tail(root), hosts, "-e", json.dumps(extra))


def test_a_vm_that_sends_more_than_a_save_may_hold_is_neither_released_nor_deleted(overfull):
    root, report = overfull
    assert (root / "overfull" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "overfull", *CLAUDE_CALLS)
    (why,) = [
        result["ansible_facts"]["claude_vm_delete_failure"]
        for name, host, result in _results(report)
        if name == "Keep the VM, whose sessions could not be saved"
    ]
    assert (
        "the VM sent more than the 102 bytes of files destroy_sessions_max_size allows; what arrived before is in"
        in (why)
    )
    assert MARKER not in json.dumps(report)


@pytest.fixture(scope="module")
def unreleased(tmp_path_factory, fake_github, fake_tailscale):
    """destroy_release: false, the save on: a VM signed in to Claude, with sessions of its own."""
    root = tmp_path_factory.mktemp("unreleased")
    hosts = _hosts(root, {"held": {"claude": True, "sessions": True}}, fake_github, fake_tailscale)
    extra = {"ansible_become": False, "destroy_release": False}
    return root, _run(root, _destroy_tail(root), hosts, "-e", json.dumps(extra))


def test_without_the_release_the_save_still_reaches_the_vm_first(unreleased):
    root, report = unreleased
    assert report["rc"] == 0, _failures(report)
    saved = _saved(root, "held")
    assert (saved / "projects" / VM_FOLDER / f"{STARTED}.jsonl").exists()
    # Nothing is released: still signed in.
    assert (root / "held" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "held", *CLAUDE_CALLS)
    said = _said(report)["held"]
    assert said.startswith("held: deleted without being released (destroy_release: false): its Tailscale device")
    assert said.endswith(
        f"Claude Code sessions: 2 saved (1 started there, 1 continued there), with the auto memory of 1 project, in"
        f" {saved} - its sessions.json has the `claude --resume <path>` that continues each here, with Claude Code"
        " 2.1.308 or newer, and what they hold is the VM's writing; left out 2 entries of the VM that were a link,"
        " not a plain file or folder, or not named as Claude Code names its own."
    )


@pytest.fixture(scope="module")
def unsudo(tmp_path_factory, fake_github, fake_tailscale):
    """A VM that answers as its user but whose sudo refuses, as one asking for a password does without a terminal."""
    root = tmp_path_factory.mktemp("unsudo")
    hosts = _hosts(root, {"unsudo": {"claude": True, "sessions": True}}, fake_github, fake_tailscale)
    sudo = root / "bin" / "sudo"
    sudo.write_text('#!/bin/sh\necho "sudo: a password is required" >&2\nexit 1\n')
    sudo.chmod(0o755)
    hosts["unsudo"]["ansible_become_exe"] = str(sudo)
    # The play's become stands: no -e ansible_become here.
    return root, _run(root, _destroy_tail(root), hosts)


def test_a_vm_whose_sudo_refuses_is_saved_and_deleted_unreleased_and_said_why(unsudo):
    # The save reads the VM user's own files and needs no sudo; the releases do.
    root, report = unsudo
    assert report["rc"] == 0, _failures(report)
    saved = _saved(root, "unsudo")
    assert (saved / "projects" / VM_FOLDER / f"{STARTED}.jsonl").exists()
    assert (root / "unsudo" / ".claude" / ".credentials.json").exists()
    assert "auth logout" not in _calls(root, "unsudo", *CLAUDE_CALLS)
    said = _said(report)["unsudo"]
    assert said.startswith("unsudo: sudo on it failed (")
    assert ", so it was not released: its Tailscale device" in said
    assert (
        "Claude Code sessions: 2 saved (1 started there, 1 continued there), with the auto memory of 1 project," in said
    )
    assert f" in {saved} - its sessions.json" in said


# ------------------------------------------------------------- destroy.yml ---
def _action(task: dict) -> str:
    (action,) = [key for key in task if "." in key]
    return action


def test_destroy_releases_before_it_deletes_and_starts_a_stopped_vm_only_to_release_it():
    plays = yaml.safe_load(DESTROY.read_text())
    names = [play.get("name") for play in plays]
    assert names.index("Release each VM from what it signed in to") < names.index("Delete the VMs on Proxmox")
    release = plays[names.index("Release each VM from what it signed in to")]
    assert release["hosts"] == "claude_vm_doomed"
    # How it reaches each VM is the host's own (add_host), never the play's:
    # a play's vars apply to its delegated tasks too, and sent the Tailscale
    # API's credential to the VM.
    assert not any(name.startswith("ansible_") for name in release.get("vars", {})), release.get("vars")
    add = next(task for task in _every_task(plays[0]["tasks"]) if task["name"] == "Add each VM as a host to delete")
    assert {"ansible_connection", "ansible_host", "ansible_user"} <= set(add["ansible.builtin.add_host"])
    delete = plays[names.index("Delete the VMs on Proxmox")]
    assert delete["vars"]["ansible_connection"] == "local"
    confirm = plays[0]
    start = next(
        task for task in confirm["tasks"] if task["name"] == "Start each stopped VM, to release it before it goes"
    )
    assert start["when"] == "claude_vm_reaching | bool"
    assert "selectattr('status', 'equalto', 'stopped')" in start["loop"]
    # Started for the release, or for the save alone, which destroy_release: false still makes.
    assert confirm["vars"]["claude_vm_reaching"] == (
        "{{ destroy_release | default(true) | bool or destroy_save_sessions | default(true) | bool }}"
    )
    release = plays[names.index("Release each VM from what it signed in to")]
    tasks = [task["name"] for task in release["tasks"]]
    assert tasks.index("Leave the VM signed in, as destroy_release asks") == (
        tasks.index("Save what of the VM's Claude Code sessions is only there") + 1
    )


def test_the_save_comes_before_every_release_and_a_failed_one_keeps_the_vm():
    plays = yaml.safe_load(DESTROY.read_text())
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    names = [task["name"] for task in release["tasks"]]
    at = names.index("Save what of the VM's Claude Code sessions is only there")
    assert at == names.index("Leave a VM that did not answer as it is") + 2
    assert at < names.index("Sign Claude Code out, and stop Remote Control")
    save = release["tasks"][at]
    # The condition on the tasks inside the block, never the block, whose rescue it would also be.
    pause, run = save["block"]
    assert pause["ansible.builtin.include_role"] == {"name": "claude_code", "tasks_from": "release_pause"}
    assert run["ansible.builtin.import_tasks"] == "tasks/session_save.yml" and "when" not in save
    assert pause["when"][0] == run["when"] == "destroy_save_sessions | default(true) | bool"
    # A VM kept: the reason first, while ansible_failed_result is the save's, then Remote Control again.
    keep, resume, end = save["rescue"]
    assert "claude_vm_delete_failure" in keep["ansible.builtin.set_fact"]
    assert resume["ansible.builtin.include_role"] == {"name": "claude_code", "tasks_from": "release_resume"}
    assert end["ansible.builtin.meta"] == "end_host"
    # Reached without sudo for the save. Sudo is asked for before it, in a block: a sudo that fails is
    # an error failed_when never sees, which would take the VM out of the run unsaved and unreported.
    reach = release["tasks"][names.index("Reach the VM")]
    assert reach["become"] is False and release["become"] is True
    sudo = release["tasks"][names.index("Ask whether sudo works on the VM")]
    assert names.index("Ask whether sudo works on the VM") == at - 1
    assert [_action(task) for task in sudo["block"]] == ["ansible.builtin.ping"] and "become" not in sudo["block"][0]
    assert "claude_vm_release_blocked" in sudo["rescue"][0]["ansible.builtin.set_fact"]
    assert pause["when"][1] == "claude_vm_release_blocked is not defined"
    end = release["tasks"][names.index("Leave a VM whose sudo failed unreleased")]
    assert end["when"] == "claude_vm_release_blocked is defined"
    assert (
        at
        < names.index("Leave a VM whose sudo failed unreleased")
        < names.index("Sign Claude Code out, and stop Remote Control")
    )
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["destroy_save_sessions"] is True and defaults["destroy_sessions_dir"] == ""


# Every task session_save.yml may hold, by action: where it runs, and how.
SAVE_ON_VM = {"ansible.builtin.getent", "ansible.builtin.stat", "claude_sessions_info"}
SAVE_HERE = {"claude_sessions_fetch", "ansible.builtin.copy"}
SAVE_ANYWHERE = {"ansible.builtin.set_fact", "ansible.builtin.assert"}


def test_the_save_reads_the_vm_under_no_log_and_writes_only_here_as_this_user(tmp_path):
    tasks = yaml.safe_load((REPO / "playbooks" / "tasks" / "session_save.yml").read_text())
    for at, task in enumerate(tasks):
        (action,) = set(task) - {
            "name",
            "vars",
            "register",
            "when",
            "delegate_to",
            "become",
            "no_log",
            "ignore_unreachable",
        }
        assert action in SAVE_ON_VM | SAVE_HERE | SAVE_ANYWHERE, (task["name"], action)
        if action in SAVE_ON_VM:
            # As the VM user, never sudo; and one that stops answering fails into destroy's rescue, never
            # out of the run unseen, which an unreachable host does.
            assert "delegate_to" not in task and task["become"] is False, task["name"]
            assert task["ignore_unreachable"] is True, task["name"]
            after = tasks[at + 1]
            assert (
                "ansible.builtin.assert" in after
                and f"{task['register']}.unreachable" in after["ansible.builtin.assert"]["that"]
            )
        if action == "claude_sessions_info":
            assert task["no_log"] is True, task["name"]
        if action in SAVE_HERE:
            assert task["delegate_to"] == "localhost" and task["become"] is False, task["name"]
    # By default into the checkout's .sessions/, which git ignores and the tracked-file guard refuses;
    # in a test, always into its own directory.
    settle = next(task for task in tasks if task["name"] == "session_save | Settle where on each machine")
    assert "(playbook_dir | dirname) ~ '/.sessions'" in settle["ansible.builtin.set_fact"]["claude_vm_save_dir"]
    assert ".sessions/" in (REPO / ".gitignore").read_text().splitlines()
    tail = yaml.safe_load(_destroy_tail(tmp_path).read_text())
    assert tail[0]["vars"]["destroy_sessions_dir"] == str(tmp_path / "saved")
    # The scenario that runs destroy.yml, into Molecule's ephemeral directory.
    provision = yaml.safe_load((REPO / "molecule" / "provision" / "group_vars" / "all" / "fake_api.yml").read_text())
    assert provision["destroy_sessions_dir"].startswith(
        "{{ lookup('ansible.builtin.env', 'MOLECULE_EPHEMERAL_DIRECTORY') }}/"
    )


def test_a_vm_started_for_its_release_is_stopped_again_when_its_deletion_fails():
    plays = yaml.safe_load(DESTROY.read_text())
    tasks = [task for play in plays for task in _every_task(play.get("tasks", []))]
    remember = next(task for task in tasks if task["name"] == "Remember which VMs this run started")
    assert "claude_vm_release_started" in remember["ansible.builtin.set_fact"]
    again = next(task for task in tasks if task["name"] == "Stop again each VM this run started but could not delete")
    assert again["ansible.builtin.import_tasks"] == "tasks/vm_stop.yml"
    assert "rejectattr('name', 'in', claude_vm_gone)" in again["vars"]["claude_vm_stop_query"]
    stop = yaml.safe_load((REPO / "playbooks" / "tasks" / "vm_stop.yml").read_text())[0]
    assert stop["ansible.builtin.uri"]["url"].endswith("/status/stop") and stop["no_log"] is True
    # After the deletion, and first in its play: nothing there that fails
    # before it can keep a VM running.
    names = [play.get("name") for play in plays]
    holder = next(play for play in plays if again in play.get("tasks", []))
    assert names.index(holder["name"]) > names.index("Delete the VMs on Proxmox")
    assert holder["tasks"][0] is again
    # The VMs gone are the play's, read from what the delete play recorded of each.
    assert "selectattr('claude_vm_deleted', 'defined')" in holder["vars"]["claude_vm_gone"]
    assert "map('extract', hostvars)" in holder["vars"]["claude_vm_doomed_vars"]


def test_a_deletion_that_fails_is_raised_only_after_the_stop_and_the_report():
    # A play in which every host fails ends the run: a deletion raised there,
    # for the one VM asked for, left that VM running and said nothing of it.
    plays = yaml.safe_load(DESTROY.read_text())
    delete = next(play for play in plays if play.get("name") == "Delete the VMs on Proxmox")
    assert "roles" not in delete and "post_tasks" not in delete
    # A VM the release play kept, its sessions unsaved, ends here; every other is deleted.
    kept, step = delete["tasks"]
    assert kept["ansible.builtin.meta"] == "end_host" and kept["when"] == "claude_vm_delete_failure is defined"
    assert step["block"][0]["ansible.builtin.import_role"] == {"name": "proxmox_vm"}
    assert step["block"][0]["vars"] == {"proxmox_vm_state": "absent"}
    assert step["block"][-1]["ansible.builtin.set_fact"] == {"claude_vm_deleted": True}
    assert "claude_vm_delete_failure" in step["rescue"][0]["ansible.builtin.set_fact"]
    last = plays[-1]["tasks"][-1]
    assert last["name"] == "Fail for each VM that was not deleted"
    assert "claude_vm_delete_failure" in last["vars"]["claude_vm_not_deleted"]
    # The release play leaves a VM it cannot use as it is, failing it never.
    release = next(play for play in plays if play.get("name") == "Release each VM from what it signed in to")
    reach = next(task for task in release["tasks"] if task["name"] == "Reach the VM")
    assert reach["failed_when"] is False and reach["ignore_unreachable"] is True


def test_a_run_that_fails_before_deleting_stops_again_what_it_started():
    plays = yaml.safe_load(DESTROY.read_text())
    tasks = plays[0]["tasks"]
    names = [task["name"] for task in tasks]
    guarded = tasks[names.index("Read where each VM is now, and add each as a host")]
    assert names.index("Remember which VMs this run started") < names.index(guarded["name"])
    inside = [task["name"] for task in guarded["block"]]
    assert inside == [
        "Read the configuration of each VM started",
        "Wait for each started VM's guest agent and its NIC's address",
        "List the VMs again, with the ones just started",
        "Read the address of each VM to release",
        # Into claude_vms alone, so that what its groups give it is read below, as the publishers read it.
        "Put each VM in claude_vms",
        # A host that forbids forwarding to them fails here, which stops what the run started and deletes nothing.
        "Check the route to each VM",
        "Add each VM as a host to delete",
        "Carry each VM's host key over to its identity",
        "Hold each VM to the route",
    ]
    # Straight after the start: nothing between them that could fail.
    assert names.index("Remember which VMs this run started") == (
        names.index("Start each stopped VM, to release it before it goes") + 1
    )
    stop, fail = guarded["rescue"]
    assert stop["ansible.builtin.import_tasks"] == "tasks/vm_stop.yml"
    assert stop["vars"]["claude_vm_stop_query"] == "{{ claude_vm_release_started }}"
    assert "ansible.builtin.fail" in fail


def test_the_wait_for_a_started_vm_is_for_the_address_of_its_own_nic():
    # Any address would do before: a bridge the guest grew (docker0,
    # tailscale0) could end the wait before DHCP, and vm_addresses.yml, which
    # pairs by the NIC's MAC, then found none and the VM went unreleased.
    tasks = list(_every_task(yaml.safe_load(DESTROY.read_text())[0]["tasks"]))
    wait = next(
        task for task in tasks if task["name"] == "Wait for each started VM's guest agent and its NIC's address"
    )
    assert "guest_ipv4(claude_vm_release_mac)" in wait["until"]
    assert "get(proxmox_nic, '')" in wait["vars"]["claude_vm_release_mac"]
    assert "net_mac('')" in wait["vars"]["claude_vm_release_mac"]
    assert wait["no_log"] is True and wait["failed_when"] is False
    # Three minutes for a VM to boot, asked every five seconds; a test asks faster, with -e.
    assert wait["retries"] == 36 and wait["delay"] == "{{ claude_vm_release_agent_delay }}"
    assert yaml.safe_load(DESTROY.read_text())[0]["vars"]["claude_vm_release_agent_delay"] == 5


def test_destroy_refuses_a_duplicated_name_before_it_starts_anything():
    # The release finds a VM by its name: of two, one could be a VM that is
    # not this project's, started and reached over SSH.
    names = [task["name"] for task in yaml.safe_load(DESTROY.read_text())[0]["tasks"]]
    assert names.index("Refuse a name that more than one VM carries") < names.index(
        "Start each stopped VM, to release it before it goes"
    )


def test_destroy_lists_the_cluster_again_whatever_it_started():
    # A skipped task still overwrites its register: listing again only when a
    # VM was started left claude_vm_all without its listing whenever none was,
    # and destroy failed before it began.
    plays = yaml.safe_load(DESTROY.read_text())
    again = next(
        task
        for task in _every_task(plays[0]["tasks"])
        if task["name"] == "List the VMs again, with the ones just started"
    )
    assert "when" not in again


def test_the_host_is_asked_about_every_vm_at_once():
    # One run_once task looped over the VMs asked the host about them one
    # after another, 15 s for each that said nothing. Each VM's own task,
    # delegated here, is asked by the forks at once; only the stop of the
    # shared connection is the run's.
    plays = yaml.safe_load(DESTROY.read_text())
    release = next(play for play in plays if play["name"] == "Release each VM from what it signed in to")
    assert "serial" not in release
    tasks = {task["name"]: task for task in _every_task(release["tasks"])}
    ask = tasks["Ask the Proxmox host about the VM"]
    assert ask["delegate_to"] == "localhost" and ask["vm_route"]["name"] == "{{ inventory_hostname }}"
    assert not {"run_once", "loop", "with_items", "throttle"} & set(ask)
    assert tasks["Make sure of the connection through the Proxmox host again"]["run_once"] is True


def test_no_play_sets_how_to_reach_a_host_in_its_vars():
    # A play's vars apply to every task it delegates, so SSH settings there
    # send a task meant for this machine - and whatever it carries - to the
    # VM. Hosts get them from add_host or the inventory; a play may only force
    # the local connection, which is this machine's anyway.
    reaching = {
        "ansible_host",
        "ansible_user",
        "ansible_port",
        "ansible_ssh_private_key_file",
        "ansible_ssh_common_args",
    }
    for path in [REPO / "deploy.yml", *sorted((REPO / "playbooks").glob("*.yml"))]:
        for play in yaml.safe_load(path.read_text()) or []:
            play_vars = play.get("vars") or {}
            assert not reaching & set(play_vars), (path.name, play.get("name"), sorted(reaching & set(play_vars)))
            assert play_vars.get("ansible_connection", "local") == "local", (path.name, play.get("name"))


def test_every_add_host_that_publishes_a_vm_pins_the_vm_s_python():
    # The VM user's ssh sessions have ~/.local/bin first on PATH (common's 15- drop-in), where interpreter
    # discovery would take a python3.13 uv put there and run modules with it, as root where the play becomes
    # it. Every add_host that gives a VM an address to be reached at pins the VM's own Python instead.
    publishing = {}
    for path in [REPO / "deploy.yml", *sorted((REPO / "playbooks").glob("*.yml"))]:
        for play in yaml.safe_load(path.read_text()) or []:
            for task in _every_task(play.get("tasks") or []):
                added = task.get("ansible.builtin.add_host")
                if isinstance(added, dict) and "ansible_host" in added:
                    publishing.setdefault(path.stem, []).append(task["name"])
                    assert "claude_vm_python" in str(added.get("ansible_python_interpreter", "")), (path.stem, task)
    assert sorted(publishing) == ["code", "destroy", "discover", "golden_build", "provision"], publishing


COMMON_CONFIGURED = (
    "{{ ansible_ssh_common_args | default(lookup('ansible.builtin.config', 'ssh_common_args',"
    " plugin_type='connection', plugin_name='ssh')) }}"
)
# What destroy's VM is given: as its groups give it, once a task before has put it in claude_vms, else Ansible's.
DOOMED_GIVEN = (
    "{{ hostvars[claude_vm_name_item].ansible_ssh_common_args | default(lookup('ansible.builtin.config',"
    " 'ssh_common_args', plugin_type='connection', plugin_name='ssh')) }}"
)
# What a VM of claude_vms is given: its own as the run first published it, else as its groups give it, else Ansible's.
GIVEN_CONFIGURED = (
    "{{{{ hostvars[{vm}].claude_vm_ssh_given | default(hostvars[{vm}].ansible_ssh_common_args"
    " | default(lookup('ansible.builtin.config', 'ssh_common_args', plugin_type='connection', plugin_name='ssh'))) }}}}"
)


def test_every_add_host_that_publishes_a_fleet_vm_holds_its_host_key_under_its_identity():
    # The address is the VM's own report: a key filed under it was one the VM could have this machine forget, and a
    # VM that reported a sibling's address had a run sent there. Every publisher of a fleet VM files the key in the
    # project's own file under the VM's identity, as common args (ssh, sftp and scp all get them), with a shared
    # connection's socket of the identity's own - by address alone, a VM reporting a sibling's address rode the
    # sibling's open connection, on which no key is checked - and carries over a key kept by address before 2.6
    # before anything connects. The golden build's VM has a file of its own (.cache/golden/), and is no VM of the fleet.
    publishing = {}
    for path in [REPO / "deploy.yml", *sorted((REPO / "playbooks").glob("*.yml"))]:
        for play in yaml.safe_load(path.read_text()) or []:
            tasks = list(_every_task(play.get("tasks") or []))
            for task in tasks:
                added = task.get("ansible.builtin.add_host")
                if isinstance(added, dict) and "ansible_host" in added and added.get("groups") != "claude_vm_build":
                    publishing.setdefault(path.stem, []).append(task["name"])
                    assert "claude_vm_host_key_alias" in added, (path.stem, task["name"])
                    assert "vm_ssh_args(claude_ssh_known_hosts_file" in str(added.get("ansible_ssh_common_args")), (
                        path.stem,
                        task["name"],
                    )
                    # The common args configured after them: this host var outranks ANSIBLE_SSH_COMMON_ARGS and
                    # ansible.cfg's, which reach the VMs only through it - and, for a VM of claude_vms, the group's
                    # own, read from the VM once a task before has put it in the group, as 2.5 applied them.
                    common = " ".join(str(added["ansible_ssh_common_args"]).split())
                    if added.get("groups") == "claude_vm_doomed":
                        assert common.endswith("{{ claude_vm_release_given }}"), path.stem
                        assert " ".join(task["vars"]["claude_vm_release_given"].split()) == DOOMED_GIVEN, path.stem
                        before = tasks[: tasks.index(task)]
                        assert [
                            t["ansible.builtin.add_host"]
                            for t in before
                            if set(t.get("ansible.builtin.add_host") or {}) == {"name", "groups"}
                            and t["ansible.builtin.add_host"]["name"] == added["name"]
                        ] == [{"name": added["name"], "groups": "claude_vms"}], path.stem
                    elif added.get("groups") == "claude_vms":
                        given = next(name for name in task["vars"] if name.endswith("ssh_given_now"))
                        assert common.endswith("{{ " + given + " }}"), path.stem
                        assert added["claude_vm_ssh_given"] == "{{ " + given + " }}", path.stem
                        vm = added["name"].removeprefix("{{ ").removesuffix(" }}")
                        assert " ".join(task["vars"][given].split()) == GIVEN_CONFIGURED.format(vm=vm), path.stem
                        before = tasks[: tasks.index(task)]
                        assert [
                            t["ansible.builtin.add_host"]
                            for t in before
                            if set(t.get("ansible.builtin.add_host") or {}) == {"name", "groups"}
                            and t["ansible.builtin.add_host"]["name"] == added["name"]
                        ] == [{"name": added["name"], "groups": "claude_vms"}], path.stem
                    else:
                        assert COMMON_CONFIGURED in common, path.stem
                    assert "ansible_ssh_extra_args" not in added, (path.stem, task["name"])
                    control = " ".join(str(added.get("ansible_control_path", "")).split())
                    assert control.startswith("%(directory)s/{{ ") and control.endswith(" }}-%%r-%%p"), path.stem
                    assert "| vm_control_name(" in control, path.stem
                    # Through the Proxmox host, the hop: made afresh from the route each time, never part of what the
                    # VM is given, between the project's options and those; in its socket's name; and the words a
                    # person's ssh needs, for the lines a run prints.
                    hop = next(name for name in task["vars"] if name.endswith("hop_now") or name.endswith("_hop"))
                    assert "| vm_hop(" in task["vars"][hop], path.stem
                    words = "{{ (" + hop + ".option ~ ' ') if " + hop + ".option else '' }}{{ "
                    assert words in common and common.index(words) > common.index("vm_ssh_args("), path.stem
                    assert f", {hop}.name)" in control, path.stem
                    assert added["claude_vm_ssh_via"] == "{{ " + hop + ".via }}", path.stem
                    imports = [t.get("ansible.builtin.import_tasks") for t in tasks]
                    assert "tasks/host_keys.yml" in imports, path.stem
                    # The route checked before the VM is added, the VM held to it after its key is carried over.
                    at = tasks.index(task)
                    assert "tasks/vm_route.yml" in imports[:at], path.stem
                    assert imports.index("tasks/host_keys.yml") < imports.index("tasks/vm_route_held.yml"), path.stem
    assert sorted(publishing) == ["code", "destroy", "discover", "provision"], publishing


@pytest.mark.parametrize("extra", [{}, {"vm_name": "alpha"}, {"vm_name": "alpha, beta,"}])
def test_with_no_terminal_destroy_takes_no_for_an_answer_whatever_names_it_is_given(tmp_path, extra):
    # The prompt is templated before any host is in scope, even when -e
    # answers it: one naming an inventory variable failed every run here,
    # where the harness has none, before anything was asked.
    report = _run(tmp_path, DESTROY, {}, "-e", json.dumps(extra))
    assert report["rc"] != 0
    assert list(_failures(report).values()) == ["Abort unless confirmed: Destroy not confirmed."], report


def test_destroy_asks_under_a_block_that_names_the_vms_and_never_logs_it(tmp_path):
    # A vars_prompt never puts the terminal in raw mode: its answer is typed as soon as it asks.
    steps = [(b"Type yes to continue", b"no\n", 0, "canonical")]
    rc, output, log = _run_at_a_terminal(tmp_path, DESTROY, {}, {"vm_name": "alpha,beta"}, steps=steps)
    assert rc != 0
    block = "##  CONFIRM DELETION\r\n" + "#" * 80 + "\r\nThis deletes alpha, beta and their disks.\r\n"
    assert block in output, output
    assert "Type yes to continue [no]: " in output
    assert "Destroy not confirmed." in output
    # vars_prompt reads with input(): neither its question nor the answer reach the run log.
    assert "This deletes" not in log


def test_with_more_than_one_host_destroy_asks_naming_the_host_it_deletes_from(tmp_path):
    # A name both hosts have, the run sent to the other one by its alias: the question says whose VM goes, read from
    # the run's own inventory and the other hosts the justfile hands it - all a vars_prompt can see.
    proxmox = tmp_path / "proxmox.yml"
    proxmox.write_text(yaml.safe_dump({"proxmox": {"hosts": {"nuc": {"ansible_host": "192.0.2.20"}}}}))
    steps = [(b"Type yes to continue", b"no\n", 0, "canonical")]
    extra = {"vm_name": "alpha", "claude_hosts_other": [{"name": "pve", "address": "192.0.2.10"}]}
    rc, output, _ = _run_at_a_terminal(tmp_path, DESTROY, {}, extra, steps=steps, args=["-i", str(proxmox)])
    assert rc != 0
    assert "\r\nThis deletes alpha on nuc and its disks.\r\n" in output, output
    # With one host, as before: no host to tell apart.
    rc, output, _ = _run_at_a_terminal(
        tmp_path, DESTROY, {}, {"vm_name": "alpha"}, steps=steps, args=["-i", str(proxmox)]
    )
    assert rc != 0 and "\r\nThis deletes alpha and its disks.\r\n" in output, output


def test_destroy_release_defaults_to_on():
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    assert defaults["destroy_release"] is True
    # A credential in the vault is enough: nothing else to set, in either defaults file.
    role = yaml.safe_load((REPO / "roles" / "tailscale" / "defaults" / "main.yml").read_text())
    assert defaults["tailscale_api_auth_method"] == role["tailscale_api_auth_method"] == "auto"


def test_every_api_call_is_made_on_this_machine_and_never_logged():
    api = yaml.safe_load((REPO / "roles" / "tailscale" / "tasks" / "api_delete.yml").read_text())
    calls = [task for task in api if "ansible.builtin.uri" in task]
    assert len(calls) == 3
    for task in calls:
        assert task["delegate_to"] == "localhost" and task["become"] is False, task["name"]
        assert task["no_log"] is True, task["name"]
        # Never with a node ID the VM made up: it goes into the path.
        assert "tailscale_api_refused_node | length == 0" in task["when"], task["name"]
    # Beside them, only the refusal of such an ID, which calls nothing.
    assert [list(task)[1] for task in api if task not in calls] == ["ansible.builtin.set_fact"]
    # GitHub's release calls no API at all: it signs gh out, and says the rest.
    github = yaml.safe_load((REPO / "roles" / "github_projects" / "tasks" / "release.yml").read_text())
    assert not [task["name"] for task in github if "ansible.builtin.uri" in task]


# ------------------------------------- destroy.yml against the fake Proxmox ---
PARKED = 9001


@pytest.fixture
def fake_pve(tmp_path):
    """The fake Proxmox, holding one stopped VM of this project's, its listing lagging a start by one listing."""
    for base in _fake_pve(tmp_path):
        _pve_vm(base, PARKED, "parked")
        _pve(base, "PUT", "fake/listing-lag", listings=1)
        yield base


def _head(tmp_path, fake_pve, var="claude_vm_release_address"):
    """A directory to run destroy.yml's first play from against the fake, and that play with each VM's ``var`` after
    it, its address by default."""
    root = tmp_path / "run"
    _pve_vars(root, fake_pve)
    plays = yaml.safe_load(DESTROY.read_text())
    # Its task files where they are: the play runs from a copy here.
    confirm = json.loads(json.dumps(plays[0]).replace('"tasks/', f'"{REPO / "playbooks" / "tasks"}/'))
    playbook = root / "destroy_head.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                confirm,
                {
                    "name": "Say where each VM would be released",
                    "hosts": "claude_vm_doomed",
                    "gather_facts": False,
                    "tasks": [{"name": "Address", "ansible.builtin.debug": {"var": var}}],
                },
            ]
        )
    )
    return root, playbook


def test_a_vm_destroy_starts_is_released_though_the_listing_still_says_it_is_stopped(tmp_path, fake_pve):
    # Proxmox lists the status it last polled, up to ten seconds old. A VM that
    # booted within those seconds of destroy's start was listed as stopped
    # after it, not asked for its address, and deleted without its release.
    root, playbook = _head(tmp_path, fake_pve)
    # The fake's agent answers on its third ask: asked every fifth of a second
    # rather than every five, as a VM booting is.
    extra = {"vm_name": "parked", "confirm_destroy": "yes", "claude_vm_release_agent_delay": 0.2}
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] == 0, _failures(report)
    said = {
        host: result.get("claude_vm_release_address") for name, host, result in _results(report) if name == "Address"
    }
    assert said == {"parked": f"192.0.2.{PARKED % 200 + 50}"}
    # The listing did lag: the run's own relisting still showed it stopped.
    calls = _pve_requests(tmp_path)
    assert [c["path"] for c in calls].count("/api2/json/cluster/resources") >= 2


def test_a_vm_destroy_reaches_gets_the_common_args_configured_and_a_socket_of_its_own(tmp_path, fake_pve):
    # The host var a publisher sets outranks ANSIBLE_SSH_COMMON_ARGS and ansible.cfg's ssh_common_args: it hands
    # them on, after the project's own, which ssh keeps since they come first.
    said = "[ansible_ssh_common_args, ansible_control_path]"
    root, playbook = _head(tmp_path, fake_pve, said)
    extra = {"vm_name": "parked", "confirm_destroy": "yes", "claude_vm_release_agent_delay": 0.2}
    env = {"ANSIBLE_SSH_COMMON_ARGS": "-o ProxyJump=jump.invalid"}
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120, env=env)
    assert report["rc"] == 0, _failures(report)
    ((common, control),) = [result[said] for name, _, result in _results(report) if name == "Address"]
    words = shlex.split(common)
    assert words[-2:] == ["-o", "ProxyJump=jump.invalid"], words
    known = next(at for at, word in enumerate(words) if word.startswith("UserKnownHostsFile="))
    assert known < words.index("ProxyJump=jump.invalid") and any(word.startswith("HostKeyAlias=") for word in words)
    assert re.fullmatch(r"%\(directory\)s/aw-[0-9a-f]{12}-%%r-%%p", control), control


def test_the_claude_vms_group_s_common_args_follow_the_project_s_own_once(tmp_path):
    # 2.5 gave a VM no ssh options of its own, so ansible_ssh_common_args set on the claude_vms group - a jump host -
    # reached every run's connection; 2.6.0's host var outranked it and dropped it unsaid. A publisher reads it from
    # the VM once a task before has put it in the group, after the project's own, which ssh keeps since they come
    # first - and once, however often a run publishes the VM.
    for base in _fake_pve(tmp_path):
        _pve_vm(base, 9020, "grouped", running=True)
        root = tmp_path / "run"
        _pve_vars(root, base)
        (root / "group_vars" / "claude_vms.yml").write_text(
            yaml.safe_dump({"ansible_ssh_common_args": "-o ProxyJump=jump.invalid"})
        )
        discover = {"ansible.builtin.import_playbook": str(REPO / "playbooks" / "discover.yml")}
        say = {
            "name": "Say what the VM is given",
            "hosts": "claude_vms",
            "gather_facts": False,
            "tasks": [{"name": "Given", "ansible.builtin.debug": {"var": "ansible_ssh_common_args"}}],
        }
        playbook = root / "discover_twice.yml"
        playbook.write_text(yaml.safe_dump([discover, discover, say]))
        report = _run(root, playbook, {}, "-e", json.dumps({"vm_name": "grouped"}), timeout=120)
    assert report["rc"] == 0, _failures(report)
    (common,) = [result["ansible_ssh_common_args"] for name, _, result in _results(report) if name == "Given"]
    words = shlex.split(common)
    assert words[-2:] == ["-o", "ProxyJump=jump.invalid"] and words.count("ProxyJump=jump.invalid") == 1, words
    known = [at for at, word in enumerate(words) if word.startswith("UserKnownHostsFile=")]
    assert len(known) == 1 and known[0] < words.index("ProxyJump=jump.invalid"), words


def test_a_vm_whose_options_an_extra_var_outranks_is_refused_before_destroy_reaches_it(tmp_path, fake_pve):
    # destroy's VMs are in claude_vm_doomed, not claude_vms: the refusal reads every group a publisher adds to.
    root, playbook = _head(tmp_path, fake_pve)
    extra = {
        "vm_name": "parked",
        "confirm_destroy": "yes",
        "claude_vm_release_agent_delay": 0.2,
        "ansible_ssh_common_args": "-o ProxyJump=jump.invalid",
    }
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] != 0
    assert (
        "and deleted nothing: parked: ansible_ssh_common_args is set for it somewhere that outranks the project's own"
    ) in _failures(report)["localhost"]
    assert not [name for name, _, _ in _results(report) if name == "Address"]


def test_each_vm_released_is_told_the_names_of_the_fleet_s_other_vms_from_the_listing(tmp_path, fake_pve):
    _pve_vm(fake_pve, 9010, "web-2")
    _pve_vm(fake_pve, 9011, "elsewhere", tags="someone-else")
    root, playbook = _head(tmp_path, fake_pve, "claude_vm_release_others")
    # The checkout's other host's VMs, by the aliases that record it: one joins the same tailnet as readily. A block
    # recording a host the checkout no longer has is no one's.
    aliases = root / ".ssh" / "agent-warehouse.conf"
    aliases.parent.mkdir(parents=True, exist_ok=True)
    aliases.write_text(
        "".join(
            f"# BEGIN agent-warehouse: {name}\n# origin: {origin}\nHost {name}\n# END agent-warehouse: {name}\n"
            for name, origin in (("db-nuc", "nuc"), ("gone-host", "old"))
        )
    )
    extra = {
        "vm_name": "parked",
        "confirm_destroy": "yes",
        "destroy_release": False,
        "destroy_save_sessions": False,
        "claude_hosts_other": [{"name": "nuc", "address": "192.0.2.20"}],
        "claude_ssh_config_file": str(aliases),
    }
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] == 0, _failures(report)
    said = {
        host: result.get("claude_vm_release_others") for name, host, result in _results(report) if name == "Address"
    }
    # The fake's container carries the tag too, and is no VM; the VM it deletes is not its own sibling.
    assert list(said) == ["parked"] and {"web-2", "db-nuc"} <= set(said["parked"])
    assert not {"parked", "elsewhere", "ours-container", "gone-host"} & set(said["parked"])


# "", 0 and false too, which took the default's 4G in their place without a word.
@pytest.mark.parametrize("cap", ["lots", "0", "-1G", "", 0, False])
def test_a_cap_on_the_saved_sessions_that_is_no_size_is_refused_before_anything_starts(tmp_path, cap):
    root, playbook = _head(tmp_path, "https://127.0.0.1:9")
    extra = {"vm_name": "parked", "confirm_destroy": "yes", "destroy_sessions_max_size": cap}
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] != 0
    (failure,) = _failures(report).values()
    assert failure.startswith("Refuse a cap on the saved sessions that is not a size: destroy_sessions_max_size is")
    assert not [name for name, _, _ in _results(report) if name.startswith("cluster_vms |")]


@pytest.mark.parametrize("folder", ["saved-sessions", "./saved"])
def test_a_relative_folder_for_the_saved_sessions_is_refused_before_anything_starts(tmp_path, folder):
    # The module that writes them ran in playbooks/, where such a folder is not git-ignored. Refused
    # before Proxmox is asked anything: nothing listens at the API's address.
    root, playbook = _head(tmp_path, "https://127.0.0.1:9")
    extra = {"vm_name": "parked", "confirm_destroy": "yes", "destroy_sessions_dir": folder}
    report = _run(root, playbook, {}, "-e", json.dumps(extra), timeout=120)
    assert report["rc"] != 0
    (failure,) = _failures(report).values()
    assert failure.startswith(
        "Refuse a folder for the saved sessions that is not an absolute path: destroy_sessions_dir"
    )
    assert not [name for name, _, _ in _results(report) if name.startswith("cluster_vms |")]
