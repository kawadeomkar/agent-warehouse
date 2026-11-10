"""What `just deploy` says before it starts, rendered by deploy.yml's own tasks.

The first play looks the template up through the API; here a canned lookup
stands in for it, set as a fact, which ranks with the register it replaces.
The tasks that follow run as deploy.yml has them.

What this guards: the opening message was built in a task var named like the
lookup's register, and a register outranks a task var, so the run opened by
printing the API's raw answer instead of the sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from harness import REPO, _failures, _run
from test_filters import BUILT

LOOKUP = "Look up the template by name"
REPORT = "Report what this run will do"
WAITS = "opens a page in your browser, and the run waits for you there."


def _report(tmp_path: Path, built: bool, config: dict | None = None, **extra) -> dict:
    plays = yaml.safe_load((REPO / "deploy.yml").read_text())
    # The play that looks the template up: the one before it refuses a name no VM may have.
    first = next(play for play in plays if LOOKUP in [task["name"] for task in play.get("tasks", [])])
    tasks = first["tasks"]
    assert [t["name"] for t in tasks][0] == LOOKUP
    lookup = tasks[0]["register"]
    template = {"name": "ubuntu-24.04-cloudinit", "node": "pve", "template": 1, "config": config or BUILT}
    canned = {"proxmox_vms": [template] if built else []}
    play = {
        "hosts": "localhost",
        "gather_facts": False,
        "tasks": [{"name": "Stand in for the lookup", "ansible.builtin.set_fact": {lookup: canned}}, *tasks[1:]],
    }
    playbook = tmp_path / "deploy_report.yml"
    playbook.write_text(yaml.safe_dump([play], sort_keys=False))
    defaults = yaml.safe_load((REPO / "inventory" / "group_vars" / "all" / "defaults.yml").read_text())
    variables = {
        "proxmox_template_name": "ubuntu-24.04-cloudinit",
        "proxmox_template_vmid": defaults["proxmox_template_vmid"],
        "proxmox_node": "pve",
        "claude_vm_names": ["alpha"],
        "deploy_sign_in": True,
        "deploy_code": True,
        # The editors as the inventory defaults list them, and the editor setting as every playbook reads it, which
        # the report says the editor by.
        "claude_editors": defaults["claude_editors"],
        "claude_editor_key": defaults["claude_editor_key"],
        # Whether a template that differs is rebuilt, as the inventory defaults say, outside the role.
        "proxmox_template_rebuild": defaults["proxmox_template_rebuild"],
        # Tailscale chosen, as off is the default from 2.1: the report's sign-ins name it but where a test says off.
        "tailscale_enabled": True,
        **extra,
    }
    return _run(tmp_path, playbook, {}, "-e", json.dumps(variables))


def _said(tmp_path: Path, built: bool, config: dict | None = None, **extra) -> str:
    report = _report(tmp_path, built, config, **extra)
    assert report["rc"] == 0, report["stderr"][-2000:]
    said = [
        host["msg"]
        for play in report["plays"]
        for task in play["tasks"]
        if task["task"]["name"] == REPORT
        for host in task["hosts"].values()
    ]
    assert len(said) == 1
    assert isinstance(said[0], str), said[0]
    return said[0]


# The template as roles/proxmox_template builds it with its defaults, as Proxmox gives its config back: test_filters'
# BUILT, the one copy a proxmox_template_build bump changes.
@pytest.mark.parametrize(
    ("built", "config", "extra", "starts"),
    [
        pytest.param(
            True, None, {}, 'Template "ubuntu-24.04-cloudinit" is already built, so this run needs no SSH', id="built"
        ),
        pytest.param(
            False, None, {}, 'Template "ubuntu-24.04-cloudinit" does not exist yet; building it first', id="missing"
        ),
        pytest.param(
            True,
            {**BUILT, "net0": "virtio=BC:24:11:40:CD:BD,bridge=vmbr1", "cpu": "x86-64-v2-AES"},
            {},
            'Template "ubuntu-24.04-cloudinit" differs from its settings (cpu x86-64-v2-AES → host; bridge vmbr1 →'
            " vmbr0), so this run rebuilds it first, over SSH as root@",
            id="differs",
        ),
        pytest.param(
            True,
            {k: v for k, v in BUILT.items() if k != "description"},
            {"proxmox_template_rebuild": False},
            'Template "ubuntu-24.04-cloudinit" differs from its settings (no record of how it was built, so it was'
            " built by an earlier version of this project), and is kept as it is since proxmox_template_rebuild is"
            " false",
            id="no-record-kept",
        ),
        pytest.param(
            True,
            {**BUILT, "vga": "std"},
            {"proxmox_template_rebuild": False},
            'Template "ubuntu-24.04-cloudinit" differs from its settings (vga std → serial0), and is kept as it is'
            " since proxmox_template_rebuild is false, so this run needs no SSH to the Proxmox host.",
            id="kept",
        ),
    ],
)
def test_the_template_is_said_as_the_lookup_found_it(tmp_path, built, config, extra, starts):
    said = _said(tmp_path, built=built, config=config, **extra)
    # In words, never the lookup's raw answer.
    assert said.startswith(starts), said
    assert "proxmox_vms" not in said


def test_a_template_with_no_record_of_this_project_s_build_is_refused_and_never_rebuilt(tmp_path):
    # It may be its owner's, made by hand: a rebuild would destroy it.
    report = _report(tmp_path, built=True, config={k: v for k, v in BUILT.items() if k != "description"})
    assert report["rc"] != 0
    (failure,) = _failures(report).values()
    assert "keeps no record of being built by this project" in failure and "qm destroy" in failure, failure


@pytest.mark.parametrize("sign_in", [True, False])
def test_the_stop_for_the_sign_ins_is_said_up_front(tmp_path, sign_in):
    said = _said(tmp_path, built=True, deploy_sign_in=sign_in)
    assert (WAITS in said) is sign_in, said
    assert "\n" not in said


# A sign-in is said only where it opens a page: Claude's without an API key,
# GitHub's for the device flow, Tailscale's where no auth key joins the VM. The
# list was built by appending " and Tailscale" to "Claude, GitHub", so with a key
# the run said "signing in to Claude, GitHub: each opens a page", and an API key
# or a GitHub token, which open none, never took a name off it.
KEY = "tskey-auth-kDeploy0CNTRL-TestOnly000000000000000"


@pytest.mark.parametrize(
    ("settings", "sign_ins"),
    [
        pytest.param({}, "Claude, GitHub and Tailscale", id="none-given"),
        pytest.param({"tailscale_auth_key": KEY}, "Claude and GitHub", id="tailscale-key"),
        pytest.param({"tailscale_enabled": False}, "Claude and GitHub", id="tailscale-off"),
        pytest.param(
            {"tailscale_auth_method": "web", "tailscale_auth_key": KEY},
            "Claude, GitHub and Tailscale",
            id="tailscale-web",
        ),
        pytest.param({"tailscale_auth_method": "key"}, "Claude and GitHub", id="tailscale-key-method"),
        pytest.param({"anthropic_api_key": "an-api-key"}, "GitHub and Tailscale", id="api-key"),
        pytest.param({"github_token": "a-token"}, "Claude and Tailscale", id="github-token"),
        pytest.param({"github_auth": False}, "Claude and Tailscale", id="github-off"),
        pytest.param(
            {"github_auth_method": "web", "github_token": "a-token"}, "Claude, GitHub and Tailscale", id="github-web"
        ),
        pytest.param({"github_auth_method": "token"}, "Claude and Tailscale", id="github-token-method"),
        pytest.param({"anthropic_api_key": "an-api-key", "github_auth": False}, "Tailscale", id="one"),
    ],
)
def test_only_the_sign_ins_that_open_a_page_are_said(tmp_path, settings, sign_ins):
    said = _said(tmp_path, built=True, **settings)
    assert f"several minutes, then signing in to {sign_ins}, and opening alpha in VS Code." in said, said
    each = "Each sign-in" if "," in sign_ins or " and " in sign_ins else "The sign-in"
    assert said.endswith(f" {each} {WAITS}"), said


# The window is said in the editor chosen.
@pytest.mark.parametrize(
    ("editor", "ending"),
    [
        pytest.param(
            {},
            "Then provisioning and configuring alpha, which takes several minutes, and opening alpha in VS Code.",
            id="default",
        ),
        pytest.param({"editor": "zed"}, "which takes several minutes, and opening alpha in Zed.", id="zed"),
    ],
)
def test_a_run_with_no_page_to_open_says_no_sign_in_and_the_editor(tmp_path, editor, ending):
    settings = {"anthropic_api_key": "an-api-key", "github_token": "a-token", "tailscale_auth_key": KEY}
    said = _said(tmp_path, built=True, **editor, **settings)
    assert said.endswith(ending), said


# The VMs were joined with commas only, so two read as three things to do:
# "provisioning alpha, beta and configuring".
@pytest.mark.parametrize(
    ("names", "said_as"),
    [
        pytest.param(["alpha", "beta"], "alpha and beta", id="two"),
        pytest.param(["alpha", "beta", "gamma"], "alpha, beta and gamma", id="three"),
    ],
)
def test_several_vms_are_said_as_a_list_and_get_no_window(tmp_path, names, said_as):
    said = _said(tmp_path, built=True, claude_vm_names=names, tailscale_enabled=False)
    assert (
        f"Then provisioning and configuring {said_as}, which takes several minutes,"
        f" then signing in to Claude and GitHub. Each sign-in {WAITS}"
    ) in said, said
    assert "VS Code" not in said
