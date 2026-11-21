"""A run of the new engine: its one record and its text log, the children it starts, the kernel's file locks it holds,
its end on a signal, and a run detached as the same program in a session of its own (docs/architecture/engine.md §16.8).

It imports schema/ and fleet/, and of today's lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names;
nothing of engine/, cli/ or control/, no pydantic, and never today's claude_vm.ansible. Imported by cli/ and engine/;
nothing of today's product imports it.
"""
