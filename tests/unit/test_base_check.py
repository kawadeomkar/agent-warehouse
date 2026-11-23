"""What configure needs of a VM's base, checked first and by name (playbooks/configure.yml, and the new engine's system
play, playbooks/system.yml, which runs the same check), and the checks of a base before it is built on
(docs/architecture/lifecycle.md §4.1). The script runs here against a folder standing in for the VM, its paths pointed
there."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from harness import REPO

from claude_vm import proxmox

UBUNTU = "ID=ubuntu\nID_LIKE=debian\n"
EVERYTHING = ("apt-get", "systemctl", "cloud-init")


def _script(cloud_init: bool, playbook: str = "configure.yml") -> str:
    plays = yaml.safe_load((REPO / "playbooks" / playbook).read_text())
    (task,) = [t for play in plays for t in play.get("pre_tasks", []) if t.get("name") == "Read what the VM's base has"]
    return re.sub(r"\{\{.*?\}\}", " cloud-init" if cloud_init else "", task["ansible.builtin.raw"])


def _read(
    tmp_path: Path,
    *,
    files=("python3", "sudo"),
    commands=EVERYTHING,
    release=UBUNTU,
    cloud_init=True,
    arch: str | None = "x86_64",
    bits: str = "64",
    flags: str = "fpu sse2 avx avx2",
    marks: tuple[str, ...] = (),
    playbook: str = "configure.yml",
) -> list[str]:
    """What the script prints of a stand-in VM holding files and commands, of the machine arch (None: uname names
    none), with a userland of bits, a CPU of flags, and marks: musl's loader, ostree's or NixOS's mark; the script as
    playbook has it."""
    root = tmp_path / "vm"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    for name in files:
        (root / name).write_text("#!/bin/sh\n")
        (root / name).chmod(0o755)
    for name in commands:
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    stand_ins = {"getconf": f"echo {bits}", **({"uname": f"echo {arch}"} if arch is not None else {})}
    for name, line in stand_ins.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{line}\n")
        (bin_dir / name).chmod(0o755)
    (bin_dir / "grep").symlink_to(shutil.which("grep"))
    (root / "os-release").write_text(release)
    (root / "cpuinfo").write_text(f"processor\t: 0\nflags\t\t: {flags}\n")
    (root / "lib").mkdir()
    for mark in marks:
        {"musl": root / "lib" / "ld-musl-x86_64.so.1", "ostree": root / "ostree-booted", "nixos": root / "NIXOS"}[
            mark
        ].write_text("")
    script = (
        _script(cloud_init, playbook)
        .replace("/usr/bin/python3", str(root / "python3"))
        .replace("/usr/bin/sudo", str(root / "sudo"))
        .replace("/etc/os-release", str(root / "os-release"))
        .replace("/proc/cpuinfo", str(root / "cpuinfo"))
        .replace("/lib/ld-musl-*", f"{root}/lib/ld-musl-*")
        .replace("/run/ostree-booted", str(root / "ostree-booted"))
        .replace("/etc/NIXOS", str(root / "NIXOS"))
    )
    done = subprocess.run(
        ["/bin/sh", "-c", script], env={**os.environ, "PATH": f"{bin_dir}"}, capture_output=True, text=True, check=True
    )
    return done.stdout.strip().splitlines()


def _missing(tmp_path: Path, **kwargs) -> str:
    """What the script says is missing from a stand-in VM (_read's)."""
    (line,) = [line for line in _read(tmp_path, **kwargs) if line.startswith("base-missing:")]
    return line.removeprefix("base-missing:").strip()


def _refused(tmp_path: Path, **kwargs) -> list[str]:
    """Why the script says configure can never run on a stand-in VM (_read's), a line each."""
    return [
        line.removeprefix("base-refused:") for line in _read(tmp_path, **kwargs) if line.startswith("base-refused:")
    ]


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


@pytest.mark.parametrize(
    "machine",
    [{}, {"arch": "aarch64", "flags": "fp asimd"}, {"flags": "fpu sse2 avx"}],
)
def test_a_64_bit_x86_64_with_avx_or_an_aarch64_is_refused_nothing(tmp_path, machine):
    assert _refused(tmp_path, **machine) == []


@pytest.mark.parametrize(
    ("machine", "said"),
    [
        ({"arch": "armv7l"}, "it is armv7l, and Claude Code runs on x86_64 and aarch64 alone"),
        ({"arch": "riscv64"}, "it is riscv64"),
        ({"arch": "i686", "bits": "32"}, "it is i686"),
        ({"arch": None}, "it is a machine uname does not name"),
        ({"arch": "aarch64", "bits": "32", "flags": ""}, "its userland is 32-bit"),
        ({"flags": "fpu sse2 sse4_2"}, "its CPU has no AVX, which Claude Code needs: give the VM the CPU type host"),
        ({"marks": ("musl",)}, "its C library is musl"),
        ({"marks": ("ostree",)}, "its packages are an image's (ostree or NixOS)"),
        ({"marks": ("nixos",)}, "its packages are an image's (ostree or NixOS)"),
    ],
)
def test_a_machine_configure_can_never_run_on_is_refused_by_name(tmp_path, machine, said):
    refused = _refused(tmp_path, **machine)
    assert any(said in line for line in refused), refused
    # Said apart from what is missing, which a base can be given.
    assert _missing(tmp_path / "again", **machine) == ""


def test_cloud_init_is_not_asked_for_where_it_is_not_waited_for(tmp_path):
    assert _missing(tmp_path, commands=("apt-get", "systemctl"), cloud_init=False) == ""


@pytest.mark.parametrize(
    "machine",
    [
        {},
        {"files": ("sudo",)},
        {"commands": ("apt-get", "systemctl")},
        {"release": 'ID=fedora\nID_LIKE="rhel"\n'},
        {"arch": "armv7l"},
        {"arch": "aarch64", "bits": "32", "flags": ""},
        {"flags": "fpu sse2 sse4_2"},
        {"marks": ("musl", "ostree")},
        {"commands": ("apt-get", "systemctl"), "cloud_init": False},
    ],
)
def test_the_system_play_checks_a_base_as_configure_does(tmp_path, machine):
    # The new engine's system play runs the same check, copied as it stands: it says of every base what configure says,
    # each stand-in's own folder apart.
    said = [line.replace(str(tmp_path / "configure"), "<vm>") for line in _read(tmp_path / "configure", **machine)]
    system = _read(tmp_path / "system", playbook="system.yml", **machine)
    assert [line.replace(str(tmp_path / "system"), "<vm>") for line in system] == said
    assert any(line.startswith("base-missing:") for line in said)


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
