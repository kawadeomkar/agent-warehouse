"""The runner over OpenSSH: ssh as a child under hard limits, a machine's host key taken at first use and filed under
its identity, one shared control directory, and the ssh children a run holds (docs/architecture/engine.md §16.6).

It imports schema/, and of today's lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names; nothing of
engine/, cli/ or control/, no pydantic, and never today's claude_vm.ansible. Imported by engine/, guest/ and the rest
of the new engine that reaches a machine; nothing of today's product imports it.
"""
