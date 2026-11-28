"""The locks each verb's run takes, as a column of the table of verbs, and the short sections a run waits for, named
here once (docs/architecture/engine.md §16.8).

`locks_for(verb, values)` reads the verb's row of the table (schema/words.py's VERBS, its column `locks`) and returns
the kind of lock a run of it takes, one of KINDS, which runs.run hands locks.take with the machines the run names:

- `machines`: with names, the fleet's lock shared and each machine's lock exclusively; with none, the fleet's lock
  exclusively. login, code, destroy and release.
- `fleet`: the fleet's lock exclusively, whatever is named. init, in every mode, and install.
- `none`: no lock of a run. status, logs, settings (a read or a write), help, cancel and mcp.

Where the column says `words`, the kind is worked out from the words given, by the function of this module that has the
verb's name, from the verb's values as the dispatch hands them (only what was given):

- `deploy`: `--check`, a dry run of configuration, takes none; any other run `machines`. So `deploy --only template`,
  which names no machine, takes the fleet's lock exclusively and is refused while any run lives, as today's one lock
  refuses it; a `deploy <name>` that finds while it runs that it must rebuild the template takes no more lock of a run
  for it, and waits in the template's section instead.
- `host`: a name (a switch, `host <name>`, or a removal, `host <name> --remove`) takes `fleet`; none (the list, its
  JSON) takes none.
- `vault`: `--rotate` takes `fleet`; anything else (the list, a put, a removal, the editor) takes none.

host and vault are no runs (the column `changes`): where their kind is `fleet`, their own handler holds the fleet's lock
exclusively for its one write through locks.exclusive, so that no run writes the vault under the key it read before a
rotation, and makes no job. Under the setting run_lock: fleet a run that takes a lock takes the fleet's exclusively
whatever its kind (locks.take's policy).

The short sections a run waits for, never refused at, are no run's lock: nobody makes a run's lock of one, and they are
not a reason for a verb to take one. Two are kernel locks of locks.py, each a file `<word>-<host>` (`section_name`)
that locks.section waits for:

- `nextid`: the next free ID asked of the host, with the clone sent onto it, exclusively.
- `template`: the host's template, exclusively around a build, a rebuild or a collection, and shared around a clone.

A run enters `nextid` outside `template`, never inside it (SECTIONS is in that order): two runs that nested them the
other way round would each wait for the other, and the sections have no time limit. The others are each write of the
managed SSH config and of the known-hosts file, of the overrides and of the vault, under store.locked by their owners,
which is why `settings` and a vault's put take no lock of a run, as today. There is no desk: two runs that sign in at
once go side by side, since a port held by another run makes Claude's forward fall back to the paste, as today, two
pages open in two tabs, and a device code copied over another is also printed at the terminal.

Standard library only, with the table and the patterns: it reads no file and imports nothing of a run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from claude_vm.schema import patterns
from claude_vm.schema.words import LOCKS, VERBS

# The column's word for a kind worked out from the words given.
WORDS = "words"
# The kinds of lock a run takes, which locks.take acts on: the column's other words.
KINDS = tuple(kind for kind in LOCKS if kind != WORDS)
MACHINES, FLEET, NONE = KINDS

# The short sections, by the word that names each one's lock file, in the order they nest: a run in one enters only
# those after it.
SECTIONS = {
    "nextid": "the next free ID and its clone, on one host: exclusively",
    "template": "the host's template: exclusively around a build, a rebuild or a collection, shared around a clone",
}
NEXTID, TEMPLATE = SECTIONS


def section_name(word: str, host: str) -> str:
    """A short section's lock file's name on host, as locks.section takes it: `<word>-<host>`, word one of SECTIONS."""
    if word not in SECTIONS:
        raise ValueError(f"{word!r} is no short section: one of {', '.join(SECTIONS)}")
    if not isinstance(host, str) or not patterns.full("current_host", host):
        raise ValueError(f"{host!r} is no host's name")
    return f"{word}-{host}"


def deploy(values: Mapping[str, object]) -> str:
    """deploy's kind: none for --check, a dry run of configuration; machines for any other run."""
    return NONE if values.get("check") is True else MACHINES


def host(values: Mapping[str, object]) -> str:
    """host's kind: fleet where a host is named, to switch to it or remove it; none for the list."""
    return FLEET if values.get("name") else NONE


def vault(values: Mapping[str, object]) -> str:
    """vault's kind: fleet for --rotate; none for anything else."""
    return FLEET if values.get("rotate") is True else NONE


# The function of each verb whose column says words, by the verb's name.
BY_WORDS: dict[str, Callable[[Mapping[str, object]], str]] = {"deploy": deploy, "host": host, "vault": vault}


def locks_for(verb: str, values: Mapping[str, object]) -> str:
    """The kind of lock a run of verb takes with these values, one of KINDS: the column's own, or for a verb whose
    column says words, its function's."""
    row = VERBS.get(verb)
    if row is None:
        raise ValueError(f"{verb!r} is no verb of the table")
    if row.locks != WORDS:
        return row.locks
    worked_out = BY_WORDS.get(verb)
    if worked_out is None:
        raise LookupError(f"the locks of {verb!r} are worked out from its words, and no function here does it")
    kind = worked_out(values)
    if kind not in KINDS:
        raise ValueError(f"the words of {verb!r} came to {kind!r}, no kind of lock")
    return kind
