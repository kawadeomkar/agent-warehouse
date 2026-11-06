"""tasks/init_local_setting.yml, which every write of local.yml by just init goes through, never writes a value that
opens a template: every later run reads local.yml as one, so {{ }}, {% %} or {# #} there would run on this machine.
Its callers hold what they write to its kind first; this is the last check before the file, whatever the value's
source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from harness import REPO, _run


# Raw, so the value the task file gets is the text itself, as an answer read back from a host would be: an expression,
# a statement and a comment, each of which opens a template.
@pytest.mark.parametrize(
    "value",
    [
        "{% raw %}{{7*7}}{% endraw %}",
        "{% raw %}{% if true %}x{% endif %}{% endraw %}",
        "{% raw %}{# x #}{% endraw %}",
    ],
    ids=["expression", "statement", "comment"],
)
def test_a_value_that_would_be_a_template_is_never_written_into_local_yml(tmp_path: Path, value: str) -> None:
    local = tmp_path / "local.yml"
    local.write_text("# proxmox_storage: local-lvm\n")
    playbook = tmp_path / "write.yml"
    task = {"ansible.builtin.include_tasks": str(REPO / "playbooks" / "tasks" / "init_local_setting.yml")}
    playbook.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False, "tasks": [task]}]))
    extra = {"init_local_file": str(local), "init_setting_key": "proxmox_storage", "init_setting_value": value}
    report = _run(tmp_path, playbook, {}, "-e", json.dumps(extra), "-c", "local")
    assert report["rc"] != 0
    assert "which every later run would read as one" in json.dumps(report)
    assert local.read_text() == "# proxmox_storage: local-lvm\n"
