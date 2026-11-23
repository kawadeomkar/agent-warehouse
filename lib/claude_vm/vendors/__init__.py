"""The vendors' side of the new engine: one HTTPS transport, which the Proxmox client uses too, Tailscale's calls, and
downloads held to their vendor's signature or digest (docs/architecture/engine.md §16.7).

It imports schema/, and of today's lib/claude_vm/ only what tests/unit/test_aw_layout.py's REUSED names; nothing of
engine/, cli/ or control/, no pydantic, and never today's claude_vm.ansible. Imported by pve/, engine/ and the rest of
the new engine that calls out; nothing of today's product imports it.
"""
