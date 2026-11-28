"""destroy's handler and the deletion of the VMs it names, the core (cards G5 and G8; docs/architecture/engine.md
§16.4): the names confirmed, each VM read again, deleted with its disks, and its SSH alias removed.

**Before the run.** The host chosen (fleet/hosts.py's reach) and the settings read for it with the run's `--set` values.
`--confirm=<names>` is held to the VMs given once more, in this process, by engine/confirm.py's `names`, the one
validator (schema/rules.py's destroy.2 refused it at the parse already). What this card does not do yet is refused, not
skipped: while `destroy_save_sessions` or `destroy_release` is true (both are, by default) nothing starts, the refusal
naming cards G13 and G14 and the two settings, so that no run loses a VM's sessions or leaves its logins. With no
terminal to ask at (runs/ask.py's `terminal()`, decided before the run draws anything) and no `--confirm`, the answer is
no: said as the person's answer, never as a failure - the question not asked, and `Nothing was deleted: the deletion
was not confirmed.` - before any job is made, exit 1 (EXITS' refused), as today's destroy takes its prompt's default.

**The run**, under each VM's lock and the fleet's shared (the verb's row: machines), the live view made first:

1. The host opened (fleet.open_for_run: the vault once, each `--set` value held to its secrets), and the VMs read
   (fleet.requested, a changing read): a name two members carry, a name no member carries (naming the pool), and a
   target the fleet does not own are refused before anything is asked; so is the run while any member of the pool
   cannot be read (whole), since one unread may carry a target's UUID. `destroy_allow_unrecorded` lifts the one refusal
   of a member with no record (pve/ownership.py's `unrecorded`), said for each such VM before the question; never a
   template, a VM outside the pool, one whose record names another UUID or one with no SMBIOS UUID.
2. The question, today's: a CONFIRM DELETION block that names the VMs, by the names Proxmox gives them, each made one
   line, their disks, and the host where the fleet has more than one, then `Type yes to continue`, whose safe answer is
   "" (no). `yes` takes it, and today's `true`, in any case; under `destroy_confirm: names`, the stricter opt-in, the
   names of the VMs typed in its place, held by `confirm.names`. `--confirm` answers it. Anything else is the person's
   answer, said in today's words, and the run ends declined (`Context.decline`, exit 1), never failed: nothing is
   deleted.
3. Each VM side by side, every one said in one line. Read again from its own endpoint whether it runs (`current`); one
   that runs is stopped (the client's `stop`, followed with the run's stopping event, so a Ctrl-C leaves it stopped and
   undeleted; sent once more where Proxmox's file lock refused it, as a stop sent seconds after a start may meet), then
   owned again through `fleet.requested` and held to the VM confirmed, its identity and VMID. Then, in the run's shield,
   which a Ctrl-C lets finish and says so: the `DELETE` (the client's `delete`, holding the configuration's identity
   and digest to the read, `destroy-unreferenced-disks` alone, no `purge`), its task followed with no stop, and the VM
   confirmed gone from `resources("vm")` - its VMID no VM's of the cluster's, or another's than the one deleted - never
   from a refusal's words. A task that ended `WARNINGS: <n>` is a success whose lines are said. Then, still in the
   shield and only for a VM said gone (a `DELETE` whose answer was lost among them), its alias's block taken out of the
   managed SSH config (engine/aliases.py's forget), unless the block records another host of the fleet, whose VM of the
   name it is: kept, and said. A removal that fails is said with the run that prunes it, and fails nothing.

The VM's line in the product's known-hosts file stays, as the tombstone of its identity; nothing here writes a record
or a session (G13). Exits 0 where every VM was deleted, 2 (EXITS' failed) where one was not, the
others gone on, 1 for a refusal, and 130 for a run a person stopped; a lock another run holds is refused by cli/main.py,
in runs/locks.py's Held's sentence. Blocking calls of the client run in threads; asyncio is imported where it is used.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..fleet import hosts, overrides, paths, sealed
from ..hosts import model
from ..pve.client import FILE, Locked, ProxmoxError, TaskFailed
from ..runs.stages import Declared, Stage, Step
from ..schema.printer import line, said
from ..schema.words import EXITS, Line
from ..text import banner, one_line
from ..vendors.transport import TransportError, Unknown
from . import aliases, confirm, fleet, report

if TYPE_CHECKING:
    from ..runs.attached import Context

PROGRAM_ROOT = Path(__file__).resolve().parents[3]
CHECK, DELETE = "check", "delete"
STOP_STEP, DELETE_STEP = "stop", "delete"
# The run's stages, as the live view draws them: a run declares only what it runs (cards G13 and G14 add theirs).
DECLARED = Declared(
    "destroy",
    (
        Stage(CHECK, "check", each_machine=False),
        Stage(DELETE, "delete", steps=(Step(STOP_STEP, "stop"), Step(DELETE_STEP, "delete"))),
        Stage(aliases.STAGE, "SSH aliases"),
    ),
)
# What the VM's own endpoint says of one that runs.
RUNNING = "running"
# What takes the question, in any case, as today's assert takes confirm_destroy.
YES = ("yes", "true")
TITLE = "CONFIRM DELETION"
# The person's answer, as today's destroy says it.
DECLINED = "Nothing was deleted: the deletion was not confirmed."
# The same with no terminal to ask at, and the line that answers it: a template said whole (said's {line}).
UNASKED = "Nothing was deleted: the deletion was not confirmed. With no terminal to ask at, {line} answers it."
# A VM's line where it was deleted: the view says it under the VM's name.
DELETED = "deleted, with its disks."
# NEXT STEPS' matter of every report destroy says (engine/report.py's say): today's, a VM not deleted leaving its line.
MATTER = "destroy"
# The settings whose work this card does not do yet, each with the card that does it.
NOT_YET = {"destroy_save_sessions": "G13", "destroy_release": "G14"}


class Failed(Exception):
    """One VM was not deleted: why, in words that follow "not deleted - "."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


@dataclasses.dataclass(frozen=True)
class How:
    """What a run's deletion was asked: --confirm's names ("" for none), what the question takes (destroy_confirm: yes
    or names), whether a member with no record may be deleted (destroy_allow_unrecorded), and the host the question
    names ("" where the fleet has one)."""

    confirmed: str = ""
    takes: str = "yes"
    unrecorded: bool = False
    host: str = ""


def _refused(sentence: str) -> int:
    print(sentence, file=sys.stderr)
    return EXITS["refused"]


def _error(error: BaseException) -> str:
    return f"error: {one_line(getattr(error, 'sentence', None) or str(error), None).removeprefix('error: ')}"


def question(names: Sequence[str], host: str = "", takes: str = "yes") -> str:
    """The deletion's question, today's: the block that names the VMs (each made one line of plain text), their disks
    and, where given, the host, then what to type."""
    listed = ", ".join(one_line(name, None) for name in names)
    on = f" on {one_line(host, None)}" if host else ""
    disks = "its disks" if len(names) == 1 else "their disks"
    typed = "yes" if takes == "yes" else "its name" if len(names) == 1 else "their names"
    return banner(f"This deletes {listed}{on} and {disks}.", TITLE) + f"Type {typed} to continue:"


def answered(answer: str, names: Sequence[str], takes: str = "yes") -> str | None:
    """None where answer confirms the deletion of names - yes (or today's true), in any case, or under takes "names" the
    names themselves, as confirm.names holds them; else what is said after DECLINED ("" for nothing more)."""
    typed = answer.strip()
    if takes == "names":
        if not typed:
            return ""
        differs = confirm.names(typed, names)
        return f" The names typed are not the VMs': {differs}." if differs else None
    return None if typed.lower() in YES else ""


def _not_yet(settings: Mapping[str, overrides.Value]) -> list[str]:
    """The settings on whose work this card does not do yet."""
    return [name for name in NOT_YET if settings[name].value is not False]


def destroy(run: Any, **values: object) -> int:
    """destroy's handler (words.py's row): refused before the run as the module says, then the deletion as its run,
    under each VM's lock. The exit status."""
    environ = dict(os.environ)
    names = tuple(str(name) for name in (values.get("names") or ()))
    words = tuple(str(word) for word in getattr(run, "set", ()) or ())
    confirmed = str(values.get("confirm") or "")
    try:
        home = paths.home(PROGRAM_ROOT, environ)
        layout = hosts.Layout(home, environ)
        reached = hosts.reach(layout, model.Request(host=str(values.get("host") or ""), names=names))
        many = layout.many()
        for note in reached.notes:
            print(f"warning: {note.text}" if note.level == "warning" else note.text, file=sys.stderr)
        given = overrides.run_values(words, secrets=(), environ=environ)
        settings = overrides.resolve(home, reached.host.name, given, environ=environ)
    except (hosts.Refused, overrides.Refused, paths.Refused, ValueError) as error:
        return _refused(_error(error))

    host_name = reached.host.name
    chosen = names or (str(settings["vm_default_name"].value),)
    host_flag = None if values.get("host") else host_name if many else ""
    if confirmed:
        differs = confirm.names(confirmed, names) if names else "no VM is given beside it"
        if differs:
            return _refused(
                f"error: --confirm names {confirmed}, but the VMs given are {','.join(names)} ({differs}): nothing"
                " was deleted"
            )
    on = _not_yet(settings)
    if on:
        cards = " and ".join(f"card {card}" for card in NOT_YET.values())
        return _refused(
            f"error: destroy does not save a VM's sessions or release it yet ({cards} build that), and"
            f" {' and '.join(on)} {'is' if len(on) == 1 else 'are'} on, so nothing was deleted: once you have saved"
            f" what you need of the VMs, set {' and '.join(NOT_YET)} to false - for this run with --set, or in the"
            " fleet's settings - and run this again"
        )
    takes = str(settings["destroy_confirm"].value)
    how = How(
        confirmed=confirmed,
        takes=takes,
        unrecorded=settings["destroy_allow_unrecorded"].value is True,
        host=host_name if many else "",
    )

    from .. import runs
    from ..runs import ask, detached, locks, textlog

    if not confirmed and ask.terminal() is None:
        asked = question(chosen, how.host, takes).splitlines()[-1].rstrip(":")
        print(ask.NOT_ASKED.format(question=asked, answer="no"), file=sys.stderr)
        way = Line("destroy", {"names": list(chosen), "confirm": ",".join(chosen)}, host_flag=host_flag)
        print(said(UNASKED, {"line": way}), file=sys.stderr)
        return EXITS["refused"]

    away = getattr(run, "detached_run", False) is True
    starter = {"kind": "detach" if away else "terminal"}
    verbosity = getattr(run, "verbosity", 0)
    # The host the run reaches, named on its line where the fleet has more than one and --host did not name it.
    title = line("destroy", values, refused=True, host_flag=host_flag)

    async def body(ctx: Context) -> int:
        import asyncio

        from ..runs import live

        live.Live(
            ctx.job,
            DECLARED,
            verbosity=verbosity if isinstance(verbosity, int) else 0,
            cancelled=ctx.cancelled,
            title=title,
            host=host_name,
        )
        try:
            host = await asyncio.to_thread(fleet.open_for_run, home, reached, words, environ)
        except (fleet.Refused, overrides.Refused, sealed.Refused, paths.Refused) as error:
            return _refused(_error(error))
        with host:
            try:
                return await delete(ctx, host, chosen, how)
            except fleet.Refused as error:
                return _refused(_error(error))

    draft = runs.Draft(
        paths.jobs_dir(home),
        "destroy",
        title,
        host_name,
        [],
        starter,
        True,
        values=values,
        lock_machines=[(host_name, name) for name in chosen],
        lock_policy=locks.policy_of(str(settings["run_lock"].value)),
        logs=textlog.logs(home, run),
        detached_limit=detached.limit_of(settings["run_detached_limit"].value),
    )
    return runs.run(draft, body)


# ---------------------------------------------------------------- the run ----


async def _call(function, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(function, *args, **kwargs)


def _read(host: fleet.Host, names: Sequence[str], how: How) -> fleet.Requested:
    """The VMs as a deletion reads them: a changing read of the whole pool, no guest agent asked."""
    return fleet.requested(host, tuple(names), change=True, unrecorded=how.unrecorded, whole=True, addresses=False)


async def delete(ctx: Context, host: fleet.Host, names: Sequence[str], how: How) -> int:
    """The deletion of names on host, as the module says (steps 1 to 3): the exit status. fleet.Refused, said by the
    handler, for a refusal before anything was asked."""
    from ..runs import ask

    ctx.emit("stage", None, CHECK)
    found = await _call(_read, host, names, how)
    for said_line in found.said:
        report.say(ctx, None, MATTER, said_line, "")
    targets = found.targets
    for target in targets:
        if target.record is None:
            report.say(
                ctx,
                None,
                MATTER,
                f"{target.said} has no record of this fleet's: it is deleted only because destroy_allow_unrecorded"
                " is on.",
                "",
            )
    if how.confirmed:
        ctx.emit("step", None, f"confirmed by --confirm: {', '.join(names)}")
    else:
        shown = [target.name for target in targets]
        answer = await ask.ask(ctx, question(shown, how.host, how.takes), "")
        why = answered(str(answer), names, how.takes)
        if why is not None:
            # The person's answer, said in today's words and never as a failure: the check done, nothing deleted, and
            # the run declined, exit 1.
            ctx.emit("stage", None, CHECK, outcome="done")
            report.say(ctx, None, MATTER, DECLINED + why, "")
            ctx.decline(DECLINED + why)
            return EXITS["refused"]
    ctx.emit("stage", None, CHECK, outcome="done")
    import asyncio

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(_one(ctx, host, target, how)) for target in targets]
    ends = [task.result() for task in tasks]
    if ctx.cancelled.is_set():
        return EXITS["cancelled"]
    return EXITS["done"] if all(end == "deleted" for end in ends) else EXITS["failed"]


def _warned(ctx: Context, key: str, status: object) -> None:
    warnings = getattr(status, "warnings", 0)
    if warnings:
        lines = " | ".join(getattr(status, "lines", ()) or ())
        kind = getattr(getattr(status, "task", None), "kind", "") or "its"
        ended = f"Proxmox's {kind} task ended with {warnings} warning(s)"
        report.say(ctx, key, MATTER, ended + (f": {lines}" if lines else ""), "")


async def _stop(ctx: Context, host: fleet.Host, owned: fleet.Owned, key: str) -> None:
    """owned stopped, as pulling its power would, its task followed with the run's stopping event: sent once more where
    Proxmox's file lock of its configuration refused it, as a stop sent seconds after a start may meet (X6)."""
    client = host.client
    for attempt in (1, 2):
        try:
            task = await _call(client.stop, owned)
            _warned(ctx, key, await _call(client.follow, task, None, ctx.stopping))
            return
        except Locked as locked:
            if locked.lock != FILE or attempt == 2:
                raise


def _again(host: fleet.Host, target: fleet.Target, how: How) -> fleet.Owned:
    """target owned again from a read made now (after its stop, which may apply pending changes to its configuration):
    through fleet.requested, never one configuration's read, and held to the VM confirmed - its identity and VMID."""
    try:
        [now] = _read(host, (target.name,), how).targets
    except fleet.Refused as refused:
        raise Failed(f"read again after its stop, {one_line(refused.sentence, None).rstrip('.')}") from None
    if (now.identity, now.vmid, now.node) != (target.identity, target.vmid, target.node) or now.owned is None:
        raise Failed(f"read again after its stop, the VM named {target.name} is no longer {target.said}, confirmed")
    return now.owned


def gone(host: fleet.Host, owned: fleet.Owned) -> bool:
    """Whether owned is gone, from the cluster's listing: no VM of the listing's at its VMID, or one whose SMBIOS UUID
    is not its own (the VMID taken again); never decided from a refusal's words. One listed there whose configuration
    cannot be read is not known to be gone."""
    listed = [entry for entry in host.client.resources("vm") if entry.vmid == owned.vmid and entry.type == fleet.QEMU]
    if not listed:
        return True
    try:
        config = host.client.config(listed[0].node, owned.vmid, host.nic)
    except (ProxmoxError, TransportError):
        return False
    return config.identity != owned.uuid


async def _one(ctx: Context, host: fleet.Host, target: fleet.Target, how: How) -> str:
    """One VM's deletion, said in one line: deleted, failed, or cancelled where the run stopped before its deletion was
    sent."""
    key, name, client = target.identity, target.name, host.client
    again = Line("destroy", {"names": [name]}, host_flag=host.host_flag)

    def step(which: str, outcome: str | None = None, why: str = "") -> None:
        extra: dict[str, object] = {"stage": DELETE}
        if outcome is not None:
            extra["outcome"] = outcome
        if why:
            extra["why"] = why
        ctx.emit("step", key, which, **extra)

    ctx.emit("stage", key, DELETE, name=name)
    try:
        owned = target.owned
        if owned is None:
            raise Failed("it was read for reading alone")
        current = await _call(client.current, target.node, target.vmid)
        if current.status == RUNNING:
            step(STOP_STEP)
            await _stop(ctx, host, owned, key)
            step(STOP_STEP, "done")
            owned = await _call(_again, host, target, how)
        if ctx.cancelled.is_set():
            raise Failed("the run was stopped before its deletion was sent")
        step(DELETE_STEP)
        # Sent, a deletion cannot be undone: a stop waits for its task's end and its look at the listing.
        async with ctx.shield(f"the deletion of {name}"):
            lost = ""
            try:
                task = await _call(client.delete, owned)
            except Unknown as error:
                # Sent, and no answer came: it may have been carried out, and is never sent again.
                task, lost = None, one_line(fleet.sentence(error), None)
            if task is not None:
                try:
                    _warned(ctx, key, await _call(client.follow, task, None, None))
                except (TaskFailed, Locked):
                    raise
                except (ProxmoxError, TransportError) as error:
                    # A task whose end is not known (its deadline, a poll that failed): the listing says.
                    lost = one_line(fleet.sentence(error), None)
            if not await _call(gone, host, owned):
                raise Failed(
                    f"its deletion's end could not be read ({lost.rstrip('.')})"
                    if lost
                    else "Proxmox's deletion task ended, but the cluster still lists it"
                )
            # Gone, so its alias would point at nothing, or at whatever takes its address next.
            forgotten = await aliases.forget(ctx, host, target, MATTER)
        step(DELETE_STEP, "done")
    except Failed as failed:
        return _not_deleted(ctx, key, failed.sentence, again)
    except (ProxmoxError, TransportError, fleet.Refused) as error:
        why = f"its deletion failed ({one_line(fleet.sentence(error), None).rstrip('.')})"
        return _not_deleted(ctx, key, why, again)
    ctx.emit("stage", key, DELETE, outcome="done")
    ctx.emit("stage", key, aliases.STAGE)
    ctx.emit("stage", key, aliases.STAGE, outcome=forgotten)
    report.say(ctx, key, MATTER, DELETED, "")
    ctx.emit("end", key, "deleted", outcome="succeeded")
    return "deleted"


def _not_deleted(ctx: Context, key: str, why: str, again: Line) -> str:
    """The VM's one line where it was not deleted, and its end: cancelled where the run was stopped, else failed."""
    text = said(
        "not deleted - {why}, so it is still there: run {line} again once that is fixed.",
        {"why": why, "line": again},
    )
    if ctx.cancelled.is_set():
        report.say(ctx, key, MATTER, text, line(again.verb, again.values, refused=False, host_flag=again.host_flag))
        ctx.emit("end", key, "stopped", outcome="cancelled")
        return "cancelled"
    ctx.emit("stage", key, DELETE, outcome="failed", why=text)
    ctx.emit("end", key, text, outcome="failed")
    return "failed"
