"""The typed client of the Proxmox VE API, the one table that names each of its calls' privilege, which init grants from
and the permission check reads, a VM's record as the text of its notes, and the base template judged from what was
read of it, in plain code that status may import (docs/architecture/engine.md §16.5).

It imports vendors/ for its one HTTPS transport, schema/, and of today's lib/claude_vm/ only what
tests/unit/test_aw_layout.py's REUSED names; nothing of engine/, cli/ or control/, no pydantic, and never today's
claude_vm.ansible. Imported by engine/ and the rest of the new engine that asks Proxmox; nothing of today's product
imports it.
"""
