"""The helper's own files: programs for a machine, sent there by guest/bundle.py on standard input and run by the
machine's own python3, never imported by the engine (docs/architecture/engine.md §16.10).

Each is held to Python 3.9's syntax and to the standard library, and none imports claude_vm, which is not on a machine:
tests/unit/test_aw_guest_helper.py and tests/unit/test_claude_vm.py hold both. main.py is the dispatcher. The probe's
three sources move here from playbooks/module_utils/ at the cutover (card K28); until then guest/bundle.py's sources()
reads them there.
"""
