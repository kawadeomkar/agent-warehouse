"""A fleet's files and its state: where they are, the hosts as folders, the typed table of settings and its overrides,
the one encrypted file of secrets, the fleet's ID and mark, and a host opened from them (docs/architecture/engine.md
§16.9).

It imports schema/, vendors/proxy.py (the one check of the setting network_proxy) and vendors/trust.py's refusal of a
file of certificates, pve/ for the client a host is opened with (opened.py), and of today's lib/claude_vm/ only what
tests/unit/test_aw_layout.py's REUSED names; nothing of engine/, cli/ or control/, no pydantic, and never today's
claude_vm.ansible. Imported by cli/, engine/, runs/ and guest/, and opened.py, a host opened, by status too once card
G16 reads the pool through it; nothing of today's product imports it.
"""
