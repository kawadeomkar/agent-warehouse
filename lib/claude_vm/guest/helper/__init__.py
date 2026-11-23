"""The helper's own files: programs for a machine, sent there by guest/bundle.py on standard input and run by the
machine's own python3, never imported by the engine (docs/architecture/engine.md §16.10).

Each is held to Python 3.10 and to the standard library, and none imports claude_vm, which is not on a machine:
tests/unit/test_aw_guest_helper.py runs the script on a Python 3.10 and parses each file with its own parser, and it and
tests/unit/test_claude_vm.py hold the rest. ruff's rules that would write past 3.10 (UP017's datetime.UTC, B905's
zip(strict=)) are kept off this folder in pyproject.toml. main.py is the dispatcher. The probe's three sources move here
from playbooks/module_utils/ at the cutover (card K28); until then guest/bundle.py's sources() reads them there.
"""
