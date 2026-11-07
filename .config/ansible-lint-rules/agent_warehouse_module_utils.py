# Copyright (c) agent-warehouse contributors
# MIT License
"""No rule: what ansible-lint's args rule needs to import the modules in playbooks/library/.

ansible-lint imports each Python file of a rules folder (rulesdir in .ansible-lint) before it lints anything. Its args
rule imports each module a task names, in ansible-lint's own process, to check the task's arguments against the
module's argument spec, and these modules import what they share from ansible.module_utils.agent_warehouse, which
Ansible finds in a playbook's module_utils folder only when it builds a module to send. Without this, the import fails,
ansible-lint passes the error over as a warning, and no task of a file that names one of these modules is checked.
"""

from pathlib import Path

import ansible.module_utils

SHARED = str(Path(__file__).resolve().parents[2] / "playbooks" / "module_utils")
if SHARED not in ansible.module_utils.__path__:
    ansible.module_utils.__path__.append(SHARED)
