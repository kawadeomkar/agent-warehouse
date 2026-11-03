"""Pins that nothing moves on its own are held to ones that something does.

Dependabot moves pyproject.toml's pins and reads no role default, so the uv every VM gets (dev_tools_uv_version) stayed
at 0.9.5 for months while uv published advisories against it - one of them a compromised root CA it bundled. The VM's
uv is held to be no older than the one the controller's lock pins.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib
import yaml

REPO = Path(__file__).resolve().parents[2]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_the_vm_s_uv_is_no_older_than_the_controller_s() -> None:
    defaults = yaml.safe_load((REPO / "roles" / "dev_tools" / "defaults" / "main.yml").read_text())
    specs = yaml.safe_load((REPO / "roles" / "dev_tools" / "meta" / "argument_specs.yml").read_text())
    dev = tomllib.loads((REPO / "pyproject.toml").read_text())["dependency-groups"]["dev"]
    controller = next(re.fullmatch(r"uv==([0-9.]+)", pin).group(1) for pin in dev if pin.startswith("uv=="))
    vm = defaults["dev_tools_uv_version"]
    assert specs["argument_specs"]["main"]["options"]["dev_tools_uv_version"]["default"] == vm
    assert _version(vm) >= _version(controller), (
        f"dev_tools_uv_version {vm} is older than pyproject.toml's uv {controller}"
    )
    assert set(defaults["dev_tools_uv_sha256"]) == set(defaults["dev_tools_uv_arch_map"])
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in defaults["dev_tools_uv_sha256"].values())
