"""The add-on: everything that exists only with a control VM, its services and its page. The base, the command line
alone, writes nothing here (docs/architecture/engine.md §16.11).

It may import the rest of the new engine, and is the one subpackage that may import pydantic; of today's
lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names, and never today's claude_vm.ansible. Nothing
outside control/ imports it, and nothing of today's product does.
"""
