"""aw host: the fleet's hosts listed, one made this computer's current host, or one removed, over fleet/hosts/
(docs/architecture/engine.md §16.9).

`host` is the handler words.py's row of host names. With no name, the list and its JSON come from today's
hosts/choose.py's `listing` and `where` over fleet/hosts.py's layout, from the files alone, no vault opened, and the
JSON is written by wire.to_wire, so its keys are today's; --for and --host beside --json say which host a run given
those names and that --host would reach. With a name, it is no run and makes no job: it takes the fleet's lock
exclusively for its one change, where runs/lock_sets.py's `locks_for` says `fleet`, through runs/locks.py's
`exclusive`, so it is refused, naming the run, while any run of the fleet lives, and a run is refused while it holds
the lock. `host <name>` writes client.yml's current_host (fleet/hosts.py's make_current). `host <name> --remove` asks
for the host's name to be typed at a terminal, refuses with none, asks before it takes the fleet's lock, so that no run
is refused while the question waits, and once the name is typed makes its checks again under that lock and deletes that
host's folder, under the lock its host.yml's writer takes: never the current host's, nor the fleet's one host's. Its VMs
stay on it and their aliases here, and its token stays in the vault, which the line it prints names (the owner's word
waits at O16 item 18). This typed confirmation is this verb's own, as today's recipe's is.

Standard library, the hosts' layout, the locks and the printer of a command at import; the YAML reader through the
layout.
"""

from __future__ import annotations

import json as json_text
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from claude_vm import store, wire
from claude_vm.fleet import hosts, overrides, paths
from claude_vm.hosts import choose, model
from claude_vm.hosts.kinds import FLEET_KINDS
from claude_vm.runs import lock_sets, locks
from claude_vm.schema import printer
from claude_vm.schema.printer import said
from claude_vm.schema.words import EXITS, PLACEHOLDER, Line

# The program's root, where fleet/paths.py works out the fleet's home from: a checkout or an installed release.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]

# The lines a sentence here names.
_INIT = Line("init")
_INIT_ANOTHER = Line("init", {"host": PLACEHOLDER, "address": PLACEHOLDER})
_SWITCH = Line("host", {"name": PLACEHOLDER})
_LIST = Line("host")

# The sentences, each said through the printer of a command.
NO_HOST = "No host yet: {init} sets up the first."
SEVERAL = "{switch} switches; --host <name> on a command reaches another host once."
ONE_HOST = "One host: {init} sets up another."
SWITCHED = "{name} is the current host: every command runs against it, and --host <name> reaches another once."
# The removal's question, on standard error, and what it says where the name is not typed, or was.
ASK = "Delete {folder}/, the fleet's files of {name}? Its VMs stay. Type {name} to delete it: "
NOT_DELETED = "Nothing was deleted."
NO_TERMINAL = "error: {remove} asks you to type the host's name, and there is no terminal to ask at"
REMOVED = (
    "Removed {folder}/, the fleet's files of {name}. Its VMs are still there on it, and their SSH aliases here. Its "
    "token stays in the vault, listed by {vault}."
)
NO_SUCH = "error: {folder} is no host's folder: {hosts} lists them"
CURRENT = "error: {name} is the current host: make another one current first, with {switch}"
ONLY = "error: {name} is the fleet's one host, which every command runs against: nothing was deleted"
LINKED = "error: {folder} is a link, where a host's folder goes: nothing is deleted through it"
UNREMOVED = "error: {folder} could not be deleted ({why}): some of it may be left, and the host with it"


def _say(notes: tuple[model.Note, ...]) -> None:
    for note in notes:
        print(f"{note.level}: {note.text}", file=sys.stderr)


def _refused(sentence: str) -> int:
    print(f"error: {sentence.removeprefix('error: ')}", file=sys.stderr)
    return EXITS["refused"]


def _names(given: object) -> tuple[str, ...]:
    """The VMs --for names: a comma-separated value, or the names the parser gave."""
    if isinstance(given, str):
        return tuple(name for name in given.split(",") if name)
    return tuple(str(name) for name in given or ())


def listing(layout: hosts.Layout, as_json: bool, names: tuple[str, ...], given: str) -> int:
    """The hosts, from their files alone, no vault opened; with --json, the document, and with --for or --host which
    host that run would reach."""
    asked = choose.where(layout, model.Request(host=given, names=names)) if names or given else None
    listed = choose.listing(layout, asked)
    if as_json:
        # A document a release only adds keys to: format_version and kind first.
        print(json_text.dumps(wire.to_wire(listed)))
        return EXITS["done"]
    problem = listed.problem or ""
    if not listed.hosts:
        print(f"{problem}." if problem else said(NO_HOST, {"init": _INIT}))
        return EXITS["done"]
    headings: list[tuple[str, str]] = []
    for kind in FLEET_KINDS.values():
        headings += [heading for heading in kind.shown if heading not in headings]
    rows = [["  HOST", "ADDRESS", *(heading for heading, _ in headings), "FOLDER", ""]]
    for row in listed.hosts:
        cells = [str(row.settings.get(name) if row.settings.get(name) is not None else "-") for _, name in headings]
        note = "the current host" if row.current else row.problem
        rows.append([f"{'▸' if row.current else ' '} {row.name}", row.address or "-", *cells, f"{row.folder}/", note])
    widths = [max(len(line[at]) for line in rows) for at in range(len(rows[0]) - 1)]
    for line in rows:
        columns = "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=False))
        print(f"{columns}  {line[-1]}" if line[-1] else columns.rstrip())
    print()
    if problem:
        print(f"{problem}.")
    if len(listed.hosts) > 1:
        print(said(SEVERAL, {"switch": _SWITCH}))
    else:
        print(said(ONE_HOST, {"init": _INIT_ANOTHER}))
    return EXITS["done"]


def switch(home: paths.Home, layout: hosts.Layout, name: str) -> int:
    """name made this computer's current host, client.yml's current_host written by its one writer."""
    expect = overrides.read(home, None, environ=layout.environ).client.digest
    hosts.make_current(layout, name, expect)
    print(said(SWITCHED, {"name": name}))
    return EXITS["done"]


def _current(layout: hosts.Layout) -> str:
    """The host a run reaches with none named: the current host, or the fleet's one host; "" where neither is."""
    try:
        found = choose.pick(layout)
    except model.Refused:
        return ""
    return found.name if found else ""


def _unremovable(layout: hosts.Layout, name: str) -> str | None:
    """Why name's folder is not removed, or None: no such host, a link, the fleet's one host or the current host."""
    shown = f"{layout.hosts_rel}/{name}"
    if model.name_refusal(name) or name not in layout.folders():
        return said(NO_SUCH, {"folder": shown, "hosts": _LIST})
    if os.path.islink(layout.hosts_dir / name):
        return said(LINKED, {"folder": shown})
    if layout.folders() == [name]:
        return said(ONLY, {"name": name})
    if _current(layout) == name:
        return said(CURRENT, {"name": name, "switch": _SWITCH})
    return None


def asked(layout: hosts.Layout, name: str) -> int | None:
    """None once name is typed at the terminal to delete its folder, else the exit status: refused where it is not to
    be removed, or the name is mistyped; cancelled by an interrupt. Asked holding no lock, so that no run of the
    fleet is refused while the question waits."""
    refusal = _unremovable(layout, name)
    if refusal is not None:
        return _refused(refusal)
    print(said(ASK, {"folder": f"{layout.hosts_rel}/{name}", "name": name}), end="", file=sys.stderr, flush=True)
    try:
        answer = sys.stdin.readline().rstrip("\n")
    except KeyboardInterrupt:
        print(f"\n{NOT_DELETED}", file=sys.stderr)
        return EXITS["cancelled"]
    if answer != name:
        print(NOT_DELETED, file=sys.stderr)
        return EXITS["refused"]
    return None


def removal(layout: hosts.Layout, name: str) -> int:
    """name's folder deleted, under the fleet's lock, once asked has had its name typed: the checks made again first,
    since the fleet may have changed while the question waited. Never the current host's, nor the one host's."""
    folder = layout.hosts_dir / name
    shown = f"{layout.hosts_rel}/{name}"
    refusal = _unremovable(layout, name)
    if refusal is not None:
        return _refused(refusal)
    # Under the lock host.yml's one writer takes, so that no write of the host's is under way as its folder goes.
    try:
        with store.locked(folder / hosts.HOST_FILE):
            shutil.rmtree(folder)
    except OSError as error:
        return _refused(said(UNREMOVED, {"folder": shown, "why": error.strerror or type(error).__name__}))
    vault = Line("vault", host_flag=name)
    print(said(REMOVED, {"folder": shown, "name": name, "vault": vault}))
    return EXITS["done"]


def host(
    run: Any,
    *,
    name: str | None = None,
    json: bool = False,
    remove: bool = False,
    for_: object = None,
    host: str | None = None,
) -> int:
    """aw host: the list, its JSON, a switch or a removal. Returns the exit status."""
    removing = remove is True
    if removing and not sys.stdin.isatty():
        return _refused(said(NO_TERMINAL, {"remove": Line("host", {"remove": True})}))
    environ = os.environ
    given: dict[str, object] = {"name": name} if name else {}
    try:
        home = paths.home(PROGRAM_ROOT, environ)
        layout = hosts.Layout(home, environ)
        if lock_sets.locks_for("host", given) != lock_sets.FLEET:
            return listing(layout, json is True, _names(for_), host or "")
        assert name is not None
        if removing and (status := asked(layout, name)) is not None:
            return status
        typed = printer.line("host", {"name": name, "remove": removing}, refused=False)
        with locks.exclusive(paths.state(home), typed):
            return removal(layout, name) if removing else switch(home, layout, name)
    except model.Refused as refused:
        _say(refused.notes)
        return _refused(str(refused))
    except (overrides.Refused, paths.Refused) as refused:
        return _refused(str(refused))
