"""What the new engine does inside a machine: the helper sent to it and never installed there, the inputs of the system
layer that Ansible converges as a child with built-in modules only, and layer 2, the rest, in plain code
(docs/architecture/engine.md §16.10).

It imports ssh/, schema/, fleet/ and runs/ (the one starter of a child, and the run's directory), and of today's
lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names; nothing of engine/, cli/ or control/, no pydantic,
and never today's claude_vm.ansible. A plugin of that Ansible child's, the one code of the new engine that imports
ansible, lives under guest/ansible/, where the child's own configuration names it. layer1.py starts that child for one
machine, and variables.py writes the play's variables it is given, every string marked unsafe. bundle.py makes the
helper's one script, of helper/'s files and the probe's, which a machine's python3 runs from its standard input.
Imported by engine/; nothing of today's product imports it.
"""
