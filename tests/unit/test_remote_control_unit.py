"""Remote Control's units render as they did for every unit a VM has: a unit whose text changed is restarted on the
next configure, which ends every Remote Control session under it, on every VM at once. Each template is rendered by
Ansible's own template module, as the role renders it, beside the one 2.5 shipped (tests/unit/units/, kept verbatim),
with the values a VM has; and with a value systemd would expand, quoted as systemd reads it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from harness import REPO, _failures, _run

TEMPLATES = REPO / "roles" / "claude_code" / "templates"
BEFORE = Path(__file__).resolve().parent / "units"
VALUES = {
    "ansible_managed": "Ansible managed",
    "claude_code_user": "dev",
    "claude_code_home": "/home/dev",
    "claude_code_remote_control_bin": "/home/dev/.local/bin/claude",
    "claude_code_remote_control_target_dir": "/home/dev/projects",
    "claude_code_remote_control_name": "alpha",
    "claude_code_remote_control_permission_mode": "default",
    "claude_code_remote_control_spawn": "same-dir",
    "claude_code_remote_control_service": "claude-remote-control",
}


def _render(tmp_path: Path, values: dict) -> dict:
    """Each unit as the template module renders it now and as 2.5's template did: {name: (now, before)}."""
    units = ("claude-remote-control.service", "claude-remote-control.path")
    tasks = [
        {
            "name": f"Render {unit} {when}",
            "ansible.builtin.template": {"src": str(src), "dest": str(tmp_path / f"{unit}.{when}"), "mode": "0644"},
        }
        for unit in units
        for when, src in (("now", TEMPLATES / f"{unit}.j2"), ("before", BEFORE / f"{unit}.2.5.j2"))
    ]
    playbook = tmp_path / "render.yml"
    playbook.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "tasks": tasks}]))
    report = _run(tmp_path, playbook, {}, "-e", json.dumps(values))
    assert report["rc"] == 0, _failures(report)
    rendered = {}
    for unit in units:
        rendered[unit] = ((tmp_path / f"{unit}.now").read_bytes(), (tmp_path / f"{unit}.before").read_bytes())
    return rendered


def test_a_vm_s_units_render_as_they_did(tmp_path):
    for unit, (now, before) in _render(tmp_path, VALUES).items():
        assert now == before, unit


@pytest.mark.parametrize(
    ("name", "said"),
    [("web 2", '--name "web 2"'), ("web%h", '--name "web%%h"'), ("a,b", "--name a,b"), ("$HOME", '--name "$$HOME"')],
)
def test_a_value_systemd_would_split_or_expand_is_quoted_as_systemd_reads_it(tmp_path, name, said):
    rendered = _render(tmp_path, {**VALUES, "claude_code_remote_control_name": name})
    service = rendered["claude-remote-control.service"][0].decode()
    (start,) = [line for line in service.splitlines() if line.startswith("ExecStart=")]
    assert said in start, start
    if "%" in name:
        (description,) = [line for line in service.splitlines() if line.startswith("Description=")]
        assert "%%" in description, description
