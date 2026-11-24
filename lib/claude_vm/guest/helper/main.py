"""The helper's dispatcher, `helper <verb> [words]`, run on a machine by its own python3 from the script that
guest/bundle.py makes (docs/architecture/engine.md §16.10).

A program for the machine, not a module the engine imports: Python 3.10 and the standard library alone, never
claude_vm, which is not there. Before it calls main, the script defines each source it packs in memory under the name
it is imported by, so a verb's module is taken from sys.modules, never from the machine's own path.

- probe: today's status probe (playbooks/module_utils/status_probe.py), unchanged, given what it is asked for: one JSON
  document a line on standard output, every program it runs one of its fixed list of reads.

A verb that is not one of these, words a verb does not take, or what it is asked not being what it reads, is one line
on standard error and exit status 2, with nothing run.
"""

from __future__ import annotations

import json
import sys

# Each verb, by the module the script defines for it, whose main(asked) runs it.
VERBS = {"probe": "ansible.module_utils.status_probe"}
# The exit status of a verb not run: it, its words or what it was asked are not what it takes.
REFUSED = 2


def _refused(text):
    """One line on standard error, the verb not run."""
    sys.stderr.write(f"helper: {text}\n")
    sys.stderr.flush()
    return REFUSED


def main(entry, asked):
    """The verb entry names, its words after it, run with asked: its exit status."""
    if not isinstance(entry, list) or not entry or not all(isinstance(word, str) for word in entry):
        return _refused("no verb given")
    verb, words = entry[0], entry[1:]
    if verb not in VERBS:
        return _refused(f"no verb {json.dumps(verb)}: the verbs are {', '.join(sorted(VERBS))}")
    if words:
        return _refused(f"{verb} takes no words")
    if not isinstance(asked, dict):
        return _refused(f"{verb} is asked for a JSON object")
    sys.modules[VERBS[verb]].main(asked)
    return 0
