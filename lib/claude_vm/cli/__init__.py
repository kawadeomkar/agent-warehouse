"""aw, the new engine's program: the command line read against the table of schema/, help, the dispatch of a verb to its
workflow, the printer of a command (docs/architecture/engine.md §16.3). main.py is the entry tools/aw runs.

It imports schema/ and the other subpackages it needs to answer at once, and of today's lib/claude_vm/ only what
tests/unit/test_aw_layout.py's REUSED names; engine/, and with it Ansible, only inside the function that dispatches a
verb that changes something, so a refusal, help or a read never loads them. Never control/, and never pydantic.
Imported only by tools/aw: nothing of today's product imports it, and the agents' server runs aw as a child.
"""
