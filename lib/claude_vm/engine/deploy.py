"""deploy's handler (cards G3, E13, G4 and G8; docs/architecture/engine.md §16.4): four stages so far, `--only
provision`, which makes or resumes each VM named (engine/create.py), `--only template`, which makes the base template
what a VM may be cloned from (engine/template.py's ensure) and stops, `--only configure`, which configures each VM
named, or every VM of the pool this fleet may change where none is (engine/configure.py), and `--only ssh-config`, which
refreshes the SSH alias of each VM named, or of every VM the fleet owns and then prunes the aliases of VMs that are gone
(engine/aliases.py's refresh); `--tags` beside VM names, with no `--only`, is `--only configure` (the one check's
deploy.7 refuses it alone). A bare deploy, the whole chain, is refused with the card that builds it, G10, and so are
`--check` (G20) and a `--tags` that names a part of the VM's second layer (G9), so that each of those cards adds its own
branch here and G10 composes them. `--remove-key` (F8) is a configure's alone: its key is taken off each VM's account
first (guest/keys.py), and refused before the run where `vm_ssh_public_keys` lists it, which the next configure would
put back, or where this computer's ssh offers it, which the run may be using.

Before the run: the size and the repositories held, the host chosen (fleet/hosts.py's reach) and the settings read for
its locks: each VM's lock under the fleet's shared, the default VM's where a provision names none; a configure that
names none reaches every VM, as does an `--only ssh-config` that names none, which prunes too, and `--only template`,
which takes no names (schema/rules.py's deploy.9), names no machine, so each of those holds the fleet's lock alone,
exclusively; and whether a person is at the terminal is decided then,
before the run draws (runs/ask.py's terminal). In the run: the live view made first, in the run's thread, then the vault
opened once - each `--set` value held to its secrets - and the host, then the stage: create.provision, template.ensure,
configure.configure or aliases.refresh. Exits 0 where every VM was provisioned or configured (or left stopped), the
aliases were refreshed, or the template is one a VM may be cloned from, 2 (EXITS' failed) where one failed, the others
gone on, or the template's build began and did not end well, 1 for a refusal before anything was created or reached,
and 130 for a run a person cancelled; a lock another run holds is refused by cli/main.py, in runs/locks.py's Held's
sentence.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..fleet import hosts, overrides, paths, sealed
from ..guest import keys
from ..hosts import model
from ..schema.printer import line, said
from ..schema.words import EXITS, Line
from ..text import one_line
from . import aliases, configure, create, fleet, routes, template

if TYPE_CHECKING:
    from ..runs.attached import Context

PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# Each stage not built yet, by --only's value, and the card that builds it; a bare deploy's (None) is the whole chain.
NOT_YET = {None: "G10"}
# Each option of deploy's not built yet, by its field, the option as typed, and the card that builds it.
OPTIONS_NOT_YET = {"adopt": ("adopt", "J7"), "take_over": ("take-over", "L25")}
# The card that builds deploy's dry run, --check.
CHECK_CARD = "G20"
STAGE = "provision"
# The stages built, each --only's value.
# --only's value for the aliases' refresh.
ALIASES = "ssh-config"
BUILT = (STAGE, template.STAGE, ALIASES)


# What each stage's run is declared as, a provision's where none is named here.
DECLARED = {template.STAGE: template.DECLARED, configure.STAGE: configure.DECLARED, ALIASES: aliases.DECLARED}


def _refused(sentence: str) -> int:
    print(sentence, file=sys.stderr)
    return EXITS["refused"]


def stage_of(values: Mapping[str, object]) -> str | None:
    """The stage a deploy runs: --only's value, else configure where --tags is given beside VM names (help's
    tags_beside_names), else None, the whole chain."""
    only = values.get("only") or None
    if only is None and values.get("tags") and values.get("names"):
        return configure.STAGE
    return only if isinstance(only, str) else None


def not_yet(values: Mapping[str, object]) -> str | None:
    """The sentence that refuses a stage of deploy no card has built yet, naming the card; None for --only provision,
    --only template, --only configure and --only ssh-config. It says none of the words given but the stage, and a
    --tags' parts of layer 2."""
    if values.get("check") is True:
        return said(
            "error: {line} is not built yet (card {card} builds it)",
            {"line": Line("deploy", {"check": True}, host_flag=""), "card": CHECK_CARD},
        )
    for field, (option, card) in OPTIONS_NOT_YET.items():
        if values.get(field) not in (None, False, ""):
            return said(
                "error: deploy's --{option} is not built yet (card {card} builds it)", {"option": option, "card": card}
            )
    only = stage_of(values)
    if values.get("remove_key") not in (None, "") and only != configure.STAGE:
        return said(
            "error: --remove-key takes a key off each VM's account in a configure alone: {configure}",
            {"configure": Line("deploy", {"only": configure.STAGE}, host_flag="")},
        )
    if only == configure.STAGE:
        return configure.unbuilt(values.get("tags"))
    if only in BUILT:
        return None
    card = NOT_YET.get(only, "G10")
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
    run, under each VM's lock, `--only configure` and `--only ssh-config` under each VM's or, naming none, the fleet's,
    or `--only template`, under the fleet's. The exit status."""
    refused = not_yet(values)
    if refused is not None:
        return _refused(refused)
    environ = dict(os.environ)
    stage = stage_of(values)
    names = tuple(str(name) for name in (values.get("names") or ()))
    words = tuple(str(word) for word in getattr(run, "set", ()) or ())
    try:
        size = create.Size.read(values.get("cores"), values.get("memory"), values.get("disk"))
        configuring = configure.Plan.read(values.get("tags"), values.get("start"), values.get("remove_key"))
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
        if configuring.remove_key:
            # Before the run: a key the list puts back, or one this computer's ssh offers, is taken off no VM.
            listed = settings["vm_ssh_public_keys"].value or ()
            why = keys.refusal(configuring.remove_key, listed, environ)
            if why is not None:
                raise keys.Refused(why)
    except (hosts.Refused, overrides.Refused, paths.Refused, ValueError) as error:
        return _refused(f"error: {one_line(str(error), None).removeprefix('error: ')}")

    from .. import runs
    from ..runs import ask, detached, locks, textlog

    host_name = reached.host.name
    templating = stage == template.STAGE
    # --only template names no machine, and a configure that names none reaches every VM, so each holds the fleet's
    # lock exclusively: no clone runs while the template builds, and no run changes a VM a configure reaches. A
    # provision names the default VM where none is named.
    if templating:
        chosen: tuple[str, ...] = ()
    elif stage in (configure.STAGE, ALIASES):
        chosen = names
    else:
        chosen = names or (str(settings["vm_default_name"].value),)
    terminal = templating and ask.terminal() is not None
    away = getattr(run, "detached_run", False) is True
    starter = {"kind": "detach" if away else "terminal"}
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
            DECLARED.get(stage, create.DECLARED),
            verbosity=verbosity if isinstance(verbosity, int) else 0,
            cancelled=ctx.cancelled,
            title=title,
            host=host_name,
        )
        try:
            host = await asyncio.to_thread(fleet.open_for_run, home, reached, words, environ)
        except (fleet.Refused, overrides.Refused, sealed.Refused, paths.Refused) as error:
            return _refused(f"error: {one_line(str(error), None).removeprefix('error: ')}")
        with host:
            if templating:
                return await _template(ctx, host, terminal)
            try:
                if stage == configure.STAGE:
                    ended = await configure.configure(ctx, host, chosen, configuring)
                    done = all(each.ok for each in ended)
                elif stage == ALIASES:
                    done = await aliases.refresh(ctx, host, chosen)
                else:
                    done = await create.provision(ctx, host, chosen, plan)
            except (fleet.Refused, routes.Refused, aliases.Refused) as error:
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
        logs=textlog.logs(home, run, part=_log_part(values)),
        detached_limit=detached.limit_of(settings["run_detached_limit"].value),
    )
    return runs.run(draft, body)


def _log_part(values: Mapping[str, object]) -> str | None:
    """The part of the run's log's name, as today's justfile names deploy's logs: check for --check, the stage --only
    names, configure for --tags; none for a whole deploy."""
    if values.get("check") is True:
        return "check"
    if values.get("only"):
        return str(values["only"])
    return "configure" if values.get("tags") else None
