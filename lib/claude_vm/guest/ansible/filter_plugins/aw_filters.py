"""The filters layer 1's child may call, and no other: plain_text, of lib/claude_vm/text.py, and sshd_match_spec, of
lib/claude_vm/checks.py, the two the system play's roles call (docs/architecture/engine.md §16.10).

Only the configuration layer 1 generates names this folder (lib/claude_vm/guest/layer1.py's FILTERS), never today's
filter_plugins/, whose filters run on this computer as a play calls them - proxmox_trust_holds opens a connection from
here to the address it is given - so that a play the child runs can call no filter of today's but these two. Neither
reads a file, runs a program or opens a connection: each makes a value of the one it is given.

An adapter, as today's are: it puts its own checkout's lib/ first on sys.path and imports the core only when a filter
is called (docs/architecture/custom-code.md §7.10).
"""

import sys
from pathlib import Path

_LIB = str(Path(__file__).resolve().parents[4])
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

# Each core module, and the filters of it the roles call.
FILTERS = {"text": ("plain_text",), "checks": ("sshd_match_spec",)}


class FilterModule:
    def filters(self):
        from claude_vm import plugin

        found = {}
        for module, names in FILTERS.items():
            found |= plugin.filters(_LIB, module, names)
        return found
