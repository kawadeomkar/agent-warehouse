"""`just deploy --tags <role>` must still discover the VMs it is meant to configure.

VMs only enter the inventory when discovery adds them, and tags propagate to
the tasks of an imported playbook, so discovery keeps running under --tags only
because configure.yml tags its import "always". Without that, `--tags
claude_code` - the documented way to push a new API key - skipped discovery,
matched no hosts and exited 0 having done nothing.

Checked with --list-tasks, which needs no hosts and runs nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from harness import ANSIBLE_PLAYBOOK, REPO, _env

DISCOVERY = "Discover VMs on Proxmox"


def _listed_tasks(root: Path, *args: str) -> dict[str, list[str]]:
    """The tasks `ansible-playbook --list-tasks` selects from configure.yml, by play name."""
    # No project config or inventory: nothing here needs them, and a
    # developer's own inventory would bring in a vault this cannot open.
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", "localhost,", "--list-tasks", *args, str(REPO / "playbooks" / "configure.yml")],
        cwd=root,
        env=_env(root),
        # As the harness runs every playbook: never pytest's terminal, and a group of its own.
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        process_group=0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    plays: dict[str, list[str]] = {}
    tasks: list[str] = []
    for line in result.stdout.splitlines():
        if line.strip().startswith("play #"):
            # "  play #1 (localhost): Discover VMs on Proxmox\tTAGS: [always]"
            tasks = plays.setdefault(line.split(": ", 1)[1].split("\t")[0], [])
        elif line.startswith("      ") and line.strip():
            tasks.append(line.strip().split("\t")[0])
    return plays


@pytest.fixture(scope="module")
def listed(tmp_path_factory) -> dict:
    """Every task, and those --tags claude_code selects: the tag that pushes a new API key. Every role tag behaves the
    same way here, so one stands for all five."""
    root = tmp_path_factory.mktemp("configure_tags")
    return {"all": _listed_tasks(root), "tagged": _listed_tasks(root, "--tags", "claude_code")}


def test_a_role_tag_keeps_every_discovery_task(listed):
    everything = listed["all"][DISCOVERY]
    assert everything, "discover.yml lists no tasks at all"
    assert listed["tagged"][DISCOVERY] == everything


# The alias play at the end, and the lookup it reads at the end of the
# configure play, are tagged the same way for the same reason: a
# `just deploy --tags claude_code` after a lease moved must still refresh
# the alias, or `ssh alpha` keeps pointing at the old address.
ALIASES = "Keep an SSH alias for each configured VM"
CONFIGURE = "Configure Claude development VMs"


def test_a_role_tag_keeps_the_alias_play_and_its_lookup(listed):
    everything, tagged = listed["all"], listed["tagged"]
    assert everything[ALIASES], "configure.yml lists no alias tasks at all"
    assert tagged[ALIASES] == everything[ALIASES]
    lookup = ["Look up the VM user's home directory", "Record where the code lives, for the alias play below"]
    assert all(task in everything[CONFIGURE] for task in lookup), everything[CONFIGURE]
    assert all(task in tagged[CONFIGURE] for task in lookup), tagged[CONFIGURE]
