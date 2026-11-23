"""What the new engine does inside a machine: the helper sent to it and never installed there, the inputs of the system
layer that Ansible converges as a child with built-in modules only, and layer 2, the rest, in plain code
(docs/architecture/engine.md §16.10).

It imports ssh/, schema/ and fleet/, and of today's lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED
names; nothing of engine/, cli/ or control/, no pydantic, and never today's claude_vm.ansible. A plugin of that Ansible
child's, the one code of the new engine that imports ansible, lives under guest/ansible/, where the child's own
configuration names it. Imported by engine/; nothing of today's product imports it.
"""
