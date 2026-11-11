"""What configure needs of a VM's base, checked first and by name (playbooks/configure.yml), and the checks of a base
before it is built on (docs/architecture/lifecycle.md §4.1). The script runs here against a folder standing in for the
VM, its paths pointed there."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import REPO

from claude_vm import proxmox

UBUNTU = "ID=ubuntu\nID_LIKE=debian\n"
EVERYTHING = ("apt-get", "systemctl", "cloud-init")


def _script(cloud_init: bool) -> str:
    plays = yaml.safe_load((REPO / "playbooks" / "configure.yml").read_text())
    (task,) = [t for play in plays for t in play.get("pre_tasks", []) if t.get("name") == "Read what the VM's base has"]
    return re.sub(r"\{\{.*?\}\}", " cloud-init" if cloud_init else "", task["ansible.builtin.raw"])


def _missing(tmp_path: Path, *, files=("python3", "sudo"), commands=EVERYTHING, release=UBUNTU, cloud_init=True) -> str:
    """What the script says is missing from a stand-in VM holding files and commands."""
    root = tmp_path / "vm"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    for name in files:
        (root / name).write_text("#!/bin/sh\n")
        (root / name).chmod(0o755)
    for name in commands:
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    (root / "os-release").write_text(release)
    script = (
        _script(cloud_init)
        .replace("/usr/bin/python3", str(root / "python3"))
        .replace("/usr/bin/sudo", str(root / "sudo"))
        .replace("/etc/os-release", str(root / "os-release"))
    )
    done = subprocess.run(
        ["/bin/sh", "-c", script], env={**os.environ, "PATH": f"{bin_dir}"}, capture_output=True, text=True, check=True
    )
    return done.stdout.strip().removeprefix("base-missing:").strip()


def test_a_base_with_everything_lacks_nothing(tmp_path):
    assert _missing(tmp_path) == ""


def test_a_debian_base_lacks_nothing(tmp_path):
    assert _missing(tmp_path, release="ID=debian\nVERSION_CODENAME=bookworm\n") == ""


def test_an_ubuntu_derivative_counts(tmp_path):
    assert _missing(tmp_path, release='ID=pop\nID_LIKE="ubuntu debian"\n') == ""


@pytest.mark.parametrize(
    ("kwargs", "said"),
    [
        ({"files": ("sudo",)}, "python3"),
        ({"files": ("python3",)}, "sudo"),
        ({"commands": ("systemctl", "cloud-init")}, "apt-get"),
        ({"commands": ("apt-get", "cloud-init")}, "systemctl"),
        ({"commands": ("apt-get", "systemctl")}, "cloud-init"),
        ({"release": 'ID=fedora\nID_LIKE="rhel"\n'}, "an-Ubuntu-or-Debian-system"),
    ],
)
def test_each_thing_a_base_lacks_is_named(tmp_path, kwargs, said):
    assert said in _missing(tmp_path, **kwargs)


def test_cloud_init_is_not_asked_for_where_it_is_not_waited_for(tmp_path):
    assert _missing(tmp_path, commands=("apt-get", "systemctl"), cloud_init=False) == ""


@pytest.mark.parametrize(
    ("text", "debian"),
    [
        (UBUNTU, True),
        ("ID=debian\n", True),
        ('ID="ubuntu"\n', True),
        ('ID=pop\nID_LIKE="ubuntu debian"\n', True),
        ('ID=fedora\nID_LIKE="rhel centos"\n', False),
        ("ID=nixos\n", False),
        ("", False),
    ],
)
def test_an_image_s_family_is_read_from_its_os_release(text, debian):
    assert proxmox.os_release_debian(text) is debian


def test_a_template_with_this_project_s_record_is_recorded_and_one_without_is_not():
    built = {"description": proxmox.proxmox_template_record({"build": 1, "image": "u", "packages": "", "sha256": "a"})}
    assert proxmox.template_recorded(built)
    assert proxmox.template_recorded({"description": "Built by claude-on-proxmox: build=1"})
    assert not proxmox.template_recorded({"description": "my own template"})
    assert not proxmox.template_recorded({})


def test_the_default_image_is_named_the_same_in_the_role_s_two_files():
    defaults = yaml.safe_load((REPO / "roles" / "proxmox_template" / "defaults" / "main.yml").read_text())
    internals = yaml.safe_load((REPO / "roles" / "proxmox_template" / "vars" / "main.yml").read_text())
    assert defaults["proxmox_template_image_url"] == internals["proxmox_template_image_url_default"]
    assert defaults["proxmox_template_image_checksum"] == internals["proxmox_template_image_checksum_default"]
