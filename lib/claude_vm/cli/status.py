"""aw status's entry (docs/architecture/engine.md §16.3): today's status engine, lib/claude_vm/status/engine.py, run in
this process with the settings cli/status_inputs.py works out from the fleet's files, in place of the ones today's
status/settings.py asks Ansible for or keeps.

`main` is the handler words.py's row of status names (the card that fills it, G16, names it there): the VMs named, the
host, the sections and --json, and the run's --set words as the top layer of the settings. A refusal is said before the
engine starts, so nothing is drawn or sent first, and the run ends as a read that failed (STATUS_EXITS' 3); then the
engine reads and draws as tools/status does, and its exit status is the run's. apt's answers for a VM are kept in the
host's folder of the fleet's state (fleet/paths.py's status_cache), as tools/status keeps them in its --cache; nothing
else is kept.

The run reads the host status_inputs picks. Today's tools/status reads every host of a checkout at once, a child each;
here that waits for the table to give status the word that starts the child (card G16), the child being this program.

Of today's status it takes one name, run, never the module whole (tests/unit/test_aw_vendor_trust.py's CONTEXTS).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from claude_vm.cli import status_inputs
from claude_vm.fleet import paths
from claude_vm.schema.words import STATUS_EXITS
from claude_vm.status.engine import run as read

# The program's root, where fleet/paths.py works out the fleet's home from: a checkout or an installed release.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]


def main(
    run: Any,
    *,
    vm_name: Sequence[str] = (),
    default_vm: bool = False,
    host: str | None = None,
    only: str | None = None,
    json: bool = False,
) -> int:
    """aw status: the report, on screen or as JSON with --json; its exit status (STATUS_EXITS). With no names it reads
    every VM the fleet made, which is what default_vm false says for it (cli/habits.py)."""
    environ = os.environ
    opts = status_inputs.Opts(
        names=",".join(vm_name or ()),
        only=only or "",
        json=json is True,
        set=tuple(str(word) for word in getattr(run, "set", ())),
    )
    try:
        home = paths.home(PROGRAM_ROOT, environ)
        config = status_inputs.load(home, host, opts, environ=environ, noted=_noted)
    except status_inputs.Refused as refused:
        for note in refused.notes:
            _noted(note)
        return _refused(refused.sentence)
    except paths.Refused as error:
        return _refused(str(error))
    kept = paths.status_cache(home) / config["hop"]["name"]
    opts = status_inputs.Opts(names=opts.names, only=opts.only, json=opts.json, cache=str(kept))
    return read(opts, loader=lambda _opts, _say=None: config)


def _noted(note: Any) -> None:
    print(f"{note.level}: {note.text}", file=sys.stderr)


def _refused(sentence: str) -> int:
    print(f"error: {sentence.removeprefix('error: ')}", file=sys.stderr)
    return STATUS_EXITS["failed"]
