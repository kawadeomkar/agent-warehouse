"""A fleet's files and its state: where they are, the hosts as folders, the typed table of settings and its overrides,
the one encrypted file of secrets, and the fleet's ID and mark (docs/architecture/engine.md §16.9).

It imports schema/, vendors/proxy.py (the one check of the setting network_proxy), and of today's lib/claude_vm/ only
what tests/unit/test_aw_layout.py's REUSED names; nothing of engine/, cli/ or control/, no pydantic, and never today's
claude_vm.ansible. Imported by cli/, engine/, runs/ and guest/; nothing of today's product imports it.
"""
