"""The new engine's workflows, one module a verb: what a run of deploy, destroy, login, status, code and the rest does,
in the command's own process, in place of the playbooks (docs/architecture/engine.md §16.4).

It imports the clients (pve/, ssh/, vendors/), runs/, fleet/, guest/ and schema/, and of today's lib/claude_vm/ only
what tests/unit/test_aw_layout.py's REUSED names; never cli/, control/, pydantic or today's claude_vm.ansible, and
Ansible itself only as a child process. Imported by cli/, only when a verb that changes something is dispatched; the
clients never import it, and nothing of today's product does.
"""
