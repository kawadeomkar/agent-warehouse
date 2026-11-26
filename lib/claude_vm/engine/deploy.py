"""deploy's handler (cards G3 and E13; docs/architecture/engine.md §16.4): two stages so far, `--only provision`, which
makes or resumes each VM named (engine/create.py), and `--only template`, which makes the base template what a VM may be
cloned from (engine/template.py's ensure) and stops. Every other stage is refused with the card that builds it - `--only
configure` and `--check` G4, `--only ssh-config` G8, and a bare deploy, the whole chain, G10 - so that each of those
cards adds its own branch here and G10 composes them.

Before the run: the size and the repositories held, the host chosen (fleet/hosts.py's reach) and the settings read for
its locks: each VM's lock under the fleet's shared, the default VM's where none is named; `--only template`, which takes
no names (schema/rules.py's deploy.9), names no machine, so it holds the fleet's lock exclusively, and whether a person
is at the terminal is decided then, before the run draws (runs/ask.py's terminal). In the run: the live view made first,
in the run's thread, then the vault opened once - each `--set` value held to its secrets - and the host, then
create.provision, or template.ensure. Exits 0 where every VM was provisioned, or the template is one a VM may be cloned
from, 2 (EXITS' failed) where one failed, the others gone on, or the template's build began and did not end well, 1 for
a refusal before anything was created, and 130 for a run a person cancelled; a lock another run holds is refused by
cli/main.py, in runs/locks.py's Held's sentence.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..fleet import hosts, overrides, paths, sealed, vaultkey
from ..hosts import model
from ..schema.printer import line, said
from ..schema.words import EXITS, Line
from ..text import one_line
from . import create, fleet, routes, template

if TYPE_CHECKING:
    from ..runs.attached import Context

PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# Each stage not built yet, by --only's value, and the card that builds it; a bare deploy's (None) is the whole chain.
NOT_YET = {"configure": "G4", "ssh-config": "G8", None: "G10"}
# Each option of deploy's not built yet, by its field, the option as typed, and the card that builds it.
OPTIONS_NOT_YET = {"adopt": ("adopt", "J7"), "take_over": ("take-over", "L25"), "remove_key": ("remove-key", "G4")}
STAGE = "provision"
# The stages built, each --only's value.
BUILT = (STAGE, template.STAGE)


def _refused(sentence: str) -> int:
    print(sentence, file=sys.stderr)
    return EXITS["refused"]


def not_yet(values: Mapping[str, object]) -> str | None:
    """The sentence that refuses a stage of deploy no card has built yet, naming the card; None for --only provision
    and --only template. It says none of the words given but the stage."""
    if values.get("check") is True:
        return said(
            "error: {line} is not built yet (card {card} builds it)",
            {"line": Line("deploy", {"check": True}, host_flag=""), "card": "G4"},
        )
    for field, (option, card) in OPTIONS_NOT_YET.items():
        if values.get(field) not in (None, False, ""):
            return said(
                "error: deploy's --{option} is not built yet (card {card} builds it)", {"option": option, "card": card}
            )
    only = values.get("only") or None
    if only in BUILT:
        return None
    card = NOT_YET.get(only if isinstance(only, str) else None, "G10")
    if only is None:
        return said(
            "error: {deploy} runs its whole chain once card {card} builds it: until then, {provision} makes a VM",
            {
                "deploy": Line("deploy", {}, host_flag=""),
                "card": card,
                "provision": Line("deploy", {"only": STAGE}, host_flag=""),
            },
        )
    return said(
        "error: {line} is not built yet (card {card} builds it)",
        {"line": Line("deploy", {"only": only}, host_flag=""), "card": card},
    )


def _opened(home: paths.Home, reached: hosts.Reached, words: Sequence[str], environ: Mapping[str, str]) -> fleet.Host:
    """The host the run is against, opened with the vault opened once: each --set value held to every secret the vault
    holds (none of which the run's record may keep) before the settings are resolved with it."""
    name = reached.host.name
    first = overrides.run_values(words, secrets=(), environ=environ)
    values = overrides.resolve(home, name, first, environ=environ)
    key = vaultkey.open(
        home, key_file=values["vault_key_file"].value, key_command=values["vault_key_command"].value, environ=environ
    )
    vault = sealed.read(home, key) or {}
    run = overrides.run_values(words, secrets=tuple(vault.values()), environ=environ)
    values = overrides.resolve(home, name, run, environ=environ)
    return fleet.connect(home, reached, values, environ=environ, vault=vault)


def _leaves(group: BaseExceptionGroup) -> list[BaseException]:
    found: list[BaseException] = []
    for each in group.exceptions:
        found.extend(_leaves(each) if isinstance(each, BaseExceptionGroup) else [each])
    return found


async def _template(ctx: Context, host: fleet.Host, terminal: bool) -> int:
    """`deploy --only template`: the base template made what a VM may be cloned from, and nothing after it."""
    settings = {name: value.value for name, value in host.values.items()}
    try:
        await template.ensure(ctx, host, settings, terminal=terminal)
    except template.Failed as error:
        print(f"error: {one_line(str(error), None)}", file=sys.stderr)
        return EXITS["failed"]
    except template.Refused as error:
        return _refused(f"error: {one_line(str(error), None)}")
    return EXITS["done"]


def deploy(run: Any, **values: object) -> int:
    """deploy's handler (words.py's row): the stages not built yet refused by name, then `--only provision` as its
    run, under each VM's lock, or `--only template`, under the fleet's. The exit status."""
    refused = not_yet(values)
    if refused is not None:
        return _refused(refused)
    environ = dict(os.environ)
    names = tuple(str(name) for name in (values.get("names") or ()))
    words = tuple(str(word) for word in getattr(run, "set", ()) or ())
    try:
        size = create.Size.read(values.get("cores"), values.get("memory"), values.get("disk"))
        repos = tuple(name for name in str(values.get("repos") or "").split(",") if name)
        home = paths.home(PROGRAM_ROOT, environ)
        asked = model.Request(host=str(values.get("host") or ""), names=names)
        layout = hosts.Layout(home, environ)
        reached = hosts.reach(layout, asked)
        many = layout.many()
        for note in reached.notes:
            print(f"warning: {note.text}" if note.level == "warning" else note.text, file=sys.stderr)
        given = overrides.run_values(words, secrets=(), environ=environ)
        settings = overrides.resolve(home, reached.host.name, given, environ=environ)
    except (hosts.Refused, overrides.Refused, paths.Refused, ValueError) as error:
        return _refused(f"error: {one_line(str(error), None).removeprefix('error: ')}")

    from .. import runs
    from ..runs import ask, locks

    host_name = reached.host.name
    templating = values.get("only") == template.STAGE
    # --only template names no machine, so the fleet's lock is held exclusively: no clone runs while it builds.
    chosen = () if templating else names or (str(settings["vm_default_name"].value),)
    terminal = templating and ask.terminal() is not None
    detached = getattr(run, "detached_run", False) is True
    starter = {"kind": "detach" if detached else "terminal"}
    # The agent's mark is the job's starter's (runs/job.py's STARTERS): mcp, which only card I's server starts.
    plan = create.Plan(
        size=size, repos=repos, start=values.get("start") is True, made_by_agent=starter["kind"] == "mcp"
    )
    verbosity = getattr(run, "verbosity", 0)
    # The host the run reaches, named on its line where the fleet has more than one and --host did not name it.
    title = line("deploy", values, refused=True, host_flag=None if values.get("host") else host_name if many else "")

    async def body(ctx: Context) -> int:
        import asyncio

        from ..runs import live

        live.Live(
            ctx.job,
            template.DECLARED if templating else create.DECLARED,
            verbosity=verbosity if isinstance(verbosity, int) else 0,
            cancelled=ctx.cancelled,
            title=title,
            host=host_name,
        )
        try:
            host = await asyncio.to_thread(_opened, home, reached, words, environ)
        except (fleet.Refused, overrides.Refused, sealed.Refused, paths.Refused) as error:
            return _refused(f"error: {one_line(str(error), None).removeprefix('error: ')}")
        with host:
            if templating:
                return await _template(ctx, host, terminal)
            try:
                done = await create.provision(ctx, host, chosen, plan)
            except (fleet.Refused, routes.Refused) as error:
                return _refused(f"error: {one_line(getattr(error, 'sentence', None) or str(error), None)}")
            except BaseExceptionGroup as group:
                stops, rest = group.split((routes.Refused, fleet.Refused))
                if rest is not None:
                    raise
                for each in _leaves(stops) if stops is not None else ():
                    print(f"error: {one_line(getattr(each, 'sentence', None) or str(each), None)}", file=sys.stderr)
                return EXITS["failed"]
        return EXITS["done"] if done else EXITS["failed"]

    draft = runs.Draft(
        paths.jobs_dir(home),
        "deploy",
        title,
        host_name,
        [],
        starter,
        True,
        values=values,
        lock_machines=[(host_name, name) for name in chosen],
        lock_policy=locks.policy_of(str(settings["run_lock"].value)),
    )
    return runs.run(draft, body)
