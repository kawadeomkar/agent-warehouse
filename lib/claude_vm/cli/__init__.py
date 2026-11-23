"""aw, the new engine's program: the command line read against the table of schema/, help, the dispatch of a verb to its
workflow (docs/architecture/engine.md §16.3); the printer of a command is schema/'s. main.py is the entry tools/aw
runs.

It imports schema/ and the other subpackages it needs to answer at once, and of today's lib/claude_vm/ only what
tests/unit/test_aw_layout.py's REUSED names; engine/ only inside the function that dispatches a verb that changes
something, so a refusal, help or a read never loads it. Never Ansible, which the engine only ever runs as a child;
never control/, and never pydantic. Imported by tools/aw, and by tools/aw-mcp, the agents' server's entry
(mcp_serve.py), which runs aw as a child for every run; nothing of today's product imports it.
"""
