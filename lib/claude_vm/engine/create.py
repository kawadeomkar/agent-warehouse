"""Making a VM on a host, `deploy --only provision` (card G3; docs/architecture/engine.md §16.4): each VM named is
cloned into the fleet's pool, given its record and its label, its cloud-init settings and its size, started, its address
read from its guest agent, and its SSH host key taken at first use and filed under its identity; or, made by an earlier
run that stopped before it was provisioned, resumed from where that run left it.

**The steps of one VM**, each an event of the run and each safe to run again:

1. Before anything is created: a VM with no key in vm_ssh_public_keys and no console password (the vault's
   vm_password) is refused with today's sentence; one with a password and no key is made, as today.
2. The source is engine/template.py's comparison: this engine's base template as its settings would build it, or one
   of the person's own that proxmox_template_custom names (only read and cloned). Any other is never cloned: the
   sentence says why, and the card that builds the template (E13). Nor is one with no cloud-init drive, which a VM's
   user and keys reach it by: its configuration read once, as today's custom template check reads one.
3. Under the short section `nextid-<host>`, the next free VMID asked and the clone sent onto it, in full, onto the
   fleet's storage, into the pool, with the record's first form as its notes; `template-<host>` held shared until its
   task has ended, so that a rebuild waits for it. A VMID taken - at the call, or in the task, by another run's clone
   that came first - is asked for again, and nothing is deleted there. A clone whose answer was lost is settled by
   reading that VMID until its clone's lock is gone, never cloned again under another before then; where the token
   cannot read it - a clone joins the pool only at its task's end, so a token that may read no VM outside the pool
   (VM.Audit on /vms) never sees it while it runs - or its configuration is refused, the VM fails (LOST), and nothing
   more is cloned. The task is followed to its end before anything is written to the new VM: the pool's token holds
   nothing on it until then. A clone cannot be undone, so from its send to its record's write (step 4) a stop waits
   for it, in the run's shield, and a second Ctrl-C still ends it.
4. The new member owned by this run's job and its own clone's task (fleet.owned), its SMBIOS UUID read - a VM with none
   is refused, naming what to set - and its record written (stage unfinished, no provisioned, its own list of
   repositories where --repos gives one, the agent's mark where an agent's run made it); then, in a call of its own,
   the label vm_label. The VM's events, named until now by the run's stand-in key, are joined to its identity.
5. The settings call, through the client's edit_config, which holds the VM's UUID: today's parameters (settings_call).
6. The root disk resized to its size, at the VM's create alone: a repair never resizes, since Proxmox cannot shrink a
   disk a person grew, and writes a size back in other units.
7. Its status read, and the VM started where it does not run; its network card's MAC read (a VM with none is refused
   with today's sentence) and its address waited for from its guest agent, then held against every running member's
   that the fleet owns (fleet.requested); then one record write sets provisioned, and the size the run gave it.
8. The route settled once a run (engine/routes.py); the VM's Target taken from Route.targets; for a VM this run
   created, or one resumed before its first configure, one connection in the runner's first-use mode, retried with
   the same Target while its sshd comes up, its key read back (hostkeys.taken) and filed under its identity
   (hostkeys.file, which refuses a second key for it); then its master made sure of. Every other connection is held to
   the key filed.

**Needs configuration.** `need_configuration(record, config, force)` is the one function that decides whether the
settings call, the start and its record write are made: a VM whose configuration has no cloud-init user, whose record
is still the first form or is not provisioned, or a repair (proxmox_vm_force_update). It reads no ownership. A
provisioned VM is otherwise left as it is: a stopped one stays stopped unless --start, and a second run sends no write.

**Rules kept.** --cores, --memory and --disk size a VM the run creates; an existing VM given another size is refused
before anything changes, unless it still needs configuration; a repair of a VM created with a size of its own sends no
part of it the run does not give. --repos gives a list to a VM the run creates, and adds to the list a VM has; a
finished VM that follows github_projects is refused it; nor is a provisioned VM resized on a repair, so a --disk larger
than its disk is refused there. A failure fails that VM alone, and the others go on. One after the record's write
(step 4) leaves a member without provisioned that the next run resumes; one before it - a clone whose answer was lost,
whose follow failed or whose record could not be written - leaves the clone, where it landed, in its first form, which
no later run may own: every later run's refusal says to delete it in Proxmox (pve/ownership.py).

Blocking calls of the client run in threads, a follow handed the run's stopping event, but the clone's, inside its
shield. asyncio is imported where it is used, as every module of the engine does.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .. import proxmox
from ..fleet import overrides
from ..pve import agent
from ..pve import record as block
from ..pve.answers import Task, VmConfig
from ..pve.client import Exists, ProxmoxError
from ..runs import lock_sets
from ..runs.stages import Declared, Stage, Step
from ..schema import patterns
from ..schema.printer import said
from ..schema.words import Line
from ..secret import Secret
from ..ssh import hostkeys, runner
from ..text import one_line
from ..vendors.transport import TransportError, Unknown
from . import fleet, record, routes, template

if TYPE_CHECKING:
    from ..runs.attached import Context

# The run's one stage and its steps, as the live view draws them: a run declares only what it runs.
STAGE = "provision"
STEPS = (
    Step("clone", "clone"),
    Step("settings", "settings"),
    Step("resize", "resize"),
    Step("start", "start"),
    Step("address", "address"),
    Step("key", "host key"),
)
DECLARED = Declared("deploy", (Stage(STAGE, "provision", steps=STEPS),))
# The VM's root disk, as today's role names it (proxmox_vm_disk).
DISK = "scsi0"
# How many VMIDs a clone asks for before it gives up: each one taken by another's clone that came first.
ATTEMPTS = 5
# How long the first connection is tried again while the VM's sshd comes up, and the pause between tries.
SSHD = 180.0
PAUSE = 2.0
# One connection that runs nothing but `true`, which says nothing: no limit on its silence but its whole time.
FIRST = runner.Limits(seconds=30.0, silent=None)
# How long a clone whose answer was lost is read for before its lock is gone, and the pause between reads.
SETTLING = 600.0
# How long VMID newid is looked for in the cluster's listing before nothing is taken to be there: the listing lags what
# a run just changed by up to ten seconds (pvestatd's poll).
LISTING_LAG = 15.0

UNREACHABLE = (
    "The VM would be unreachable: set vm_ssh_public_keys (it defaults to ~/.ssh/id_ed25519.pub) or vm_password, in the"
    " vault, for the cloud-init user."
)
SIZE_EXISTS = (
    "{name} exists, with {cores} cores, {memory} MiB of memory and a {disk} GiB disk. --cores, --memory and --disk size"
    " a VM this run creates, and change none that exists: give it the size it has, or none. To resize it, change it in"
    " Proxmox (the VM's Hardware) and restart it, or destroy it and deploy it again."
)
REPOS_FOLLOWS = (
    "{name} exists and follows the github_projects setting: --repos gives a list to a VM this run creates, or adds to"
    " the list a VM has. Add the repository to github_projects, or destroy the VM and deploy it again with --repos."
)
NO_MAC = (
    'Could not read a MAC address from {nic} of VM "{name}" (VMID {vmid}). The template may use a NIC other than {nic};'
    " set proxmox_nic."
)
NO_UUID = (
    "{vm} has no SMBIOS UUID in its configuration, which its identity is made from and its SSH host key is filed"
    " under: set an smbios1 UUID on it in Proxmox (the VM's Options), then run {line} again."
)
NO_CLOUD_INIT = (
    'The base template "{name}" ({vmid}) has no cloud-init drive, so a VM cloned from it would boot without its user'
    " and its keys: no VM is cloned from it. Give it one (qm set {vmid} --ide2 <storage>:cloudinit), or have it rebuilt"
    " (card E13 builds and rebuilds the template)."
)
LOST = (
    "{name}: Proxmox's answer to its clone at VMID {newid} was lost, and {why}, so whether that clone is there cannot"
    " be told, and nothing more is cloned. Once any clone at VMID {newid} has ended, delete that VM in Proxmox where it"
    " is {name}, then run this again."
)
LEFT_STOPPED = (
    'VM "{name}" (VMID {vmid}) already exists and is stopped. Re-running does not start a VM that was shut down, so it'
    " was left as it is, with no address. Start it in Proxmox, or run {line} to have this run start it."
)


class Failed(Exception):
    """One VM's step failed: the sentence that says why. The VM is left as the step found it, for the next run."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


# ---------------------------------------------------------------- what a run asks ----


@dataclasses.dataclass(frozen=True)
class Size:
    """The size a run gives each VM it creates (--cores, --memory, --disk), each part None or "" where not given: the
    memory and the disk in the capital form overrides.capital reads them in."""

    cores: int | None = None
    memory: str = ""
    disk: str = ""

    @classmethod
    def read(cls, cores: object = None, memory: object = None, disk: object = None) -> Size:
        """The size deploy's options give, each held by the table's pattern and read by the one reader of a size:
        ValueError, said without the value, for one that is not."""
        found_cores = None
        if cores not in (None, ""):
            if not isinstance(cores, (str, int)) or isinstance(cores, bool) or not patterns.full("cores", str(cores)):
                raise ValueError("--cores is a whole number from 1 to 999")
            found_cores = int(cores)
        return cls(
            found_cores,
            overrides.capital("memory", str(memory)) if memory not in (None, "") else "",
            overrides.capital("disk", str(disk)) if disk not in (None, "") else "",
        )

    @property
    def given(self) -> bool:
        return self.cores is not None or bool(self.memory) or bool(self.disk)

    @property
    def memory_mb(self) -> int | None:
        return overrides.mebibytes(self.memory) if self.memory else None


@dataclasses.dataclass(frozen=True)
class Plan:
    """What the run asks of each VM: its size, its own repositories, whether a stopped VM is started, and whether an
    agent's run makes it."""

    size: Size = Size()
    repos: tuple[str, ...] = ()
    start: bool = False
    made_by_agent: bool = False


# ---------------------------------------------------------------- the one decision, and the settings call ----


def need_configuration(found: record.Record | None, config: VmConfig, force: bool = False) -> bool:
    """Whether the VM still needs its settings call, its start and the record write after it (today's
    proxmox_vm_needs_configuration): its configuration has no cloud-init user, which only the settings call writes; its
    record is none, or still the first form; it is not provisioned; or the run repairs it (force,
    proxmox_vm_force_update). Never the stage beyond the first form, and never the ownership: what decides is the record
    and the configuration read."""
    if not isinstance(config, VmConfig):
        raise TypeError("need_configuration reads a VM's configuration as the client read it")
    if found is not None and not isinstance(found, record.Record):
        raise TypeError("need_configuration reads a VM's record, or none")
    return (
        force is True
        or not str(config.values.get("ciuser") or "")
        or found is None
        or found.stage == "cloning"
        or not found.provisioned
    )


def kept_size(found: record.Record | None, size: Size) -> tuple[str, ...]:
    """The parts of its size a VM created with a size of its own keeps on a repair, each one the run does not give
    (today's proxmox_vm_kept_size): cores, memory (and its minimum) and disk."""
    if found is None or not found.sized:
        return ()
    given = {"cores": size.cores is not None, "memory": bool(size.memory), "disk": bool(size.disk)}
    return tuple(part for part, here in given.items() if not here)


def settings_call(
    values: Mapping[str, object], size: Size, password: Secret | None, kept: Sequence[str] = ()
) -> dict[str, object]:
    """The settings call's changes, every one before the first start (today's "Apply hardware and cloud-init
    settings"): the cloud-init user and its keys, its network (DHCP unless a person set an address), the nameservers and
    the search domain where set, the start at boot, the guest agent, the cores, the memory and its minimum (the balloon)
    but the parts kept, ciupgrade=0 only where vm_first_boot_upgrade is false, and the console password where the vault
    holds one, never where it does not. values are the run's settings, by name."""
    keys = [str(key) for key in (values["vm_ssh_public_keys"] or ())]
    changes: dict[str, object] = {
        "onboot": 1,
        "agent": "enabled=1",
        "ciuser": str(values["vm_user"]),
        "ipconfig0": str(values["vm_ipconfig"]),
    }
    if keys:
        changes["sshkeys"] = "\n".join(keys)
    if password is not None:
        if not isinstance(password, Secret):
            raise TypeError("the console password is a Secret")
        changes["cipassword"] = password
    nameservers = [str(each) for each in (values["vm_nameservers"] or ())]
    if nameservers:
        changes["nameserver"] = " ".join(nameservers)
    if values["vm_searchdomain"]:
        changes["searchdomain"] = str(values["vm_searchdomain"])
    if values["vm_first_boot_upgrade"] is False:
        changes["ciupgrade"] = 0
    if "cores" not in kept:
        changes["cores"] = size.cores if size.cores is not None else int(values["vm_cores"])
    if "memory" not in kept:
        setting, least = int(values["vm_memory_mb"]), int(values["vm_memory_min_mb"])
        memory = size.memory_mb
        if memory is None:
            changes["memory"], changes["balloon"] = setting, least
        else:
            changes["memory"] = memory
            changes["balloon"] = min(least, memory) if least < setting else memory
    return changes


# ---------------------------------------------------------------- one VM ----


def _gib(config: VmConfig) -> str:
    found = proxmox.proxmox_disk_bytes(str(config.values.get(DISK) or ""))
    return f"{round(found / 1024**3, 1):g}"


def _differs(config: VmConfig, size: Size) -> bool:
    """Whether a VM that exists has another size than the run gives: cores or memory not equal, or a disk smaller."""
    values = config.values
    if size.cores is not None and int(values.get("cores") or 0) != size.cores:
        return True
    if size.memory_mb is not None and int(values.get("memory") or 0) != size.memory_mb:
        return True
    return _short(config, size)


def _short(config: VmConfig, size: Size) -> bool:
    """Whether the run gives a --disk larger than the VM's root disk."""
    if not size.disk:
        return False
    return proxmox.proxmox_disk_bytes(str(config.values.get(DISK) or "")) < proxmox.proxmox_disk_bytes(size.disk)


@dataclasses.dataclass(eq=False)
class Making:
    """One VM's provision in one run: the run's context, the host and the route, what the run asks, the template it
    clones from where it creates, the VM's name, and the key its events name it by (a stand-in until its identity is
    known)."""

    ctx: Context
    host: fleet.Host
    route: routes.Route
    plan: Plan
    source: template.Compared | None
    name: str
    key: str
    target: fleet.Target | None = None
    owned: fleet.Owned | None = None
    created: bool = False
    # What the machine's end says where it went well.
    ended_as: str = "provisioned"

    # ------------------------------------------------------------ saying it ----

    def emit(self, kind: str, text: str, **data: object) -> None:
        self.ctx.emit(kind, self.key, text, **data)

    def step(self, key: str, outcome: str | None = None, why: str = "") -> None:
        extra: dict[str, object] = {"stage": STAGE}
        if outcome is not None:
            extra["outcome"] = outcome
        if why:
            extra["why"] = why
        self.emit("step", key, **extra)

    @property
    def values(self) -> Mapping[str, object]:
        return {name: value.value for name, value in self.host.values.items()}

    def line(self, **more: object) -> Line:
        return Line("deploy", {"names": [self.name], "only": STAGE, **more}, host_flag=self.host.host_flag)

    async def call(self, function, *args, **kwargs):
        import asyncio

        return await asyncio.to_thread(function, *args, **kwargs)

    # ------------------------------------------------------------ the whole ----

    async def run(self) -> bool:
        """The VM made or resumed, and said: True where it is provisioned (or left stopped, as asked), False where a
        step failed, said. A cancellation, and a route that cannot be used, end the run."""
        self.emit("stage", STAGE, name=self.name)
        try:
            await self._run()
        except Failed as failed:
            why = one_line(failed.sentence, None)
            if self.ctx.cancelled.is_set():
                # A step the person's stop ended (the client's Stopped): the VM is left for the next run, unfailed.
                self.emit("end", "stopped", outcome="cancelled")
                return False
            self.emit("stage", STAGE, outcome="failed", why=why)
            self.emit("end", why, outcome="failed")
            return False
        self.emit("stage", STAGE, outcome="done")
        self.emit("end", self.ended_as, outcome="succeeded")
        return True

    async def _run(self) -> None:
        try:
            if self.target is None:
                await self._clone()
                if self.ctx.cancelled.is_set():
                    # A stop the clone's shield held off: the VM, owned and recorded, is left for the next run.
                    raise Failed(f"{self.name}: stopped once its clone was recorded")
            await self._converge()
        except routes.Unusable:
            # The route cannot be used: the run's stop, never one VM's.
            raise
        except routes.Refused as refused:
            raise Failed(f"{self.name}: {refused.sentence}") from None
        except (ProxmoxError, TransportError, fleet.Refused, record.Unreadable, record.TooLong) as error:
            raise Failed(f"{self.name}: {fleet.sentence(error)}") from None
        except ValueError as error:
            raise Failed(f"{self.name}: {one_line(str(error), None)}") from None

    # ------------------------------------------------------------ steps 3 and 4: the clone ----

    async def _clone(self) -> None:
        source = self.source
        assert source is not None and source.template_id is not None
        client, job = self.host.client, self.ctx.job.id
        first = record.Record(kind=fleet.KIND, name=self.name, stage="cloning", job=job)
        notes = block.render("", first.fields())
        storage = str(self.values["proxmox_storage"] or "") or None
        nextid = lock_sets.section_name(lock_sets.NEXTID, self.host.name)
        held = lock_sets.section_name(lock_sets.TEMPLATE, self.host.name)
        from ..runs import locks

        self.step("clone")
        for _ in range(ATTEMPTS):
            async with contextlib.AsyncExitStack() as shared:
                async with locks.section(self.ctx, nextid, machine=self.key):
                    await shared.enter_async_context(locks.section(self.ctx, held, shared=True, machine=self.key))
                    newid = await self.call(client.next_id)
                    # Sent, a clone cannot be undone: a stop waits for its task's end and its record's write, or it
                    # would land in its first form, which no later run may own. Entered once both sections are held,
                    # and never once the run is stopping, when nothing is sent.
                    await shared.enter_async_context(self.ctx.shield(f"the clone of {self.name}"))
                    try:
                        task = await self.call(
                            client.clone,
                            source.node,
                            source.template_id,
                            newid,
                            self.name,
                            self.host.pool,
                            full=True,
                            storage=storage,
                            description=notes,
                        )
                    except Exists:
                        continue
                    except Unknown:
                        await self._settle(newid)
                        continue
                # The VMID is taken on the node from the call on: the next run's clone asks for another. Followed with
                # no stop, inside the shield, as a deletion already sent is.
                try:
                    status = await self.call(client.follow, task, None, None)
                except Exists:
                    continue
                self._warned(status)
                await self._own(task)
            break
        else:
            raise Failed(f"{self.name} was not cloned: each of {ATTEMPTS} free VMIDs was taken before its clone landed")
        self.step("clone", "done")

    async def _settle(self, newid: int) -> None:
        """A clone whose answer was lost, settled before anything else is cloned. A clone joins the pool only once its
        task ends, so only a token that may read every VM (VM.Audit on /vms) sees VMID newid while it runs: without it,
        or where newid's configuration cannot be read, the VM fails (LOST), never cloned again. Otherwise newid is read
        until no clone's lock holds it: where a VM is there with this run's first form, its clone landed with no task
        this run can own it by, Failed, saying so; where nothing is there once the listing's lag is past, nothing of
        this run's is, and a VMID is asked for again."""
        import asyncio
        import time

        client = self.host.client
        try:
            held = (await self.call(client.permissions, fleet.EVERY_VM)).privileges
        except (ProxmoxError, TransportError):
            held = {}
        if not held.get(fleet.AUDIT):
            why = (
                f"the token may not read a VM outside the pool {self.host.pool} ({fleet.AUDIT} on {fleet.EVERY_VM},"
                " proxmox_audit_scope: pool), which a clone is until its task ends"
            )
            raise Failed(LOST.format(name=self.name, newid=newid, why=why))
        began = time.monotonic()
        end = began + SETTLING
        while True:
            found = [entry for entry in await self.call(client.resources, "vm") if entry.vmid == newid]
            if not found:
                if time.monotonic() - began > LISTING_LAG:
                    return
                await asyncio.sleep(PAUSE)
                continue
            try:
                config = await self.call(client.config, found[0].node, newid, self.host.nic)
            except (ProxmoxError, TransportError) as error:
                why = f"its configuration could not be read ({fleet.sentence(error)})"
                raise Failed(LOST.format(name=self.name, newid=newid, why=why)) from None
            if config.lock != fleet.CLONING:
                break
            if time.monotonic() > end or self.ctx.cancelled.is_set():
                raise Failed(f"{self.name}: VMID {newid} is still being cloned, and its clone's answer was lost")
            await asyncio.sleep(PAUSE)
        fields = block.parse(config.description) or {}
        if fields.get("job") == self.ctx.job.id and block.first_form(fields):
            raise Failed(
                f"{self.name}'s clone landed at VMID {newid}, but Proxmox's answer to it was lost, so this run has no"
                " task to own it by: delete that VM in Proxmox, then run this again"
            )

    def _warned(self, status: object) -> None:
        warnings = getattr(status, "warnings", 0)
        if warnings:
            lines = " | ".join(getattr(status, "lines", ()) or ())
            ended = f"{self.name}: Proxmox's task ended with {warnings} warning(s)"
            self.emit("report", ended + (f": {lines}" if lines else ""))

    async def _own(self, task: Task) -> None:
        """Step 4: the new member owned by this run's job and its own clone's task, its record and its label written,
        and its events joined to its identity."""
        members = await self.call(fleet.members, self.host)
        found = [member for member in members if member.vmid == task.vmid and member.node == task.node]
        if not found:
            raise Failed(
                f"{self.name} was cloned at VMID {task.vmid}, but is not a member of the pool {self.host.pool}"
            )
        target = found[0]
        if not target.identity:
            raise Failed(said(NO_UUID, {"vm": target.said, "line": self.line()}))
        owned = fleet.owned(target, job=self.ctx.job.id, clone=task)
        made = record.Record(
            kind=fleet.KIND,
            uuid=owned.uuid,
            name=self.name,
            stage="unfinished",
            repositories=self.plan.repos or None,
            made_by_agent=self.plan.made_by_agent,
            job=self.ctx.job.id,
        )
        await self.call(record.write, self.host.client, owned, lambda _: made)
        await self.call(record.label, self.host.client, owned, str(self.values["vm_label"]))
        self.ctx.emit("join", owned.identity, self.key)
        self.key, self.owned, self.created = owned.identity, owned, True
        self.target = await self._read()

    async def _read(self) -> fleet.Target:
        """The VM read again, as a changing read gives it: the target with its Owned, and its address where it runs."""
        requested = await self.call(fleet.requested, self.host, (self.name,), change=True)
        [target] = requested.targets
        return target

    # ------------------------------------------------------------ steps 5 to 8 ----

    async def _converge(self) -> None:
        target = self.target
        assert target is not None
        client = self.host.client
        config = await self.call(client.config, target.node, target.vmid, self.host.nic)
        found = target.record
        force = self.values["proxmox_vm_force_update"] is True
        needs = need_configuration(found, config, force)
        size = self.plan.size
        # A provisioned VM is never resized, on a repair either: a --disk larger than its disk is refused there as any
        # other size is where nothing needs configuring, or its record would hold a disk the VM does not have.
        grows = found is not None and found.provisioned and _short(config, size)
        if (not needs and size.given and _differs(config, size)) or (needs and grows):
            raise Failed(
                SIZE_EXISTS.format(
                    name=self.name,
                    cores=config.values.get("cores", "?"),
                    memory=config.values.get("memory", "?"),
                    disk=_gib(config),
                )
            )
        repos = self.plan.repos
        if repos and found is not None and found.provisioned and found.repositories is None:
            # A VM provisioned to follow the setting keeps following it: a repair (needs) gives it no list either.
            raise Failed(REPOS_FOLLOWS.format(name=self.name))
        owned = target.owned
        assert owned is not None
        self.owned = owned
        if needs:
            await self._configure(owned, found, size, config)
        elif repos and found is not None and set(repos) - set(found.repositories or ()):
            await self.call(record.write, client, owned, lambda now: _with_repos(now, repos))
        running = (await self.call(client.current, target.node, target.vmid)).status == "running"
        if not running and not needs:
            if not self.plan.start:
                filled = {"name": self.name, "vmid": target.vmid, "line": self.line(start=True)}
                self.emit("report", said(LEFT_STOPPED, filled))
                self.ended_as = "left stopped"
                return
            await self._start(owned)
        elif not running:
            await self._start(owned)
        address = await self._address()
        if needs:
            # Once its address is read (step 7): a start whose VM never answered is resumed by the next run.
            await self._provisioned(size)
        await self._key(address)

    async def _configure(self, owned: fleet.Owned, found: record.Record | None, size: Size, config: VmConfig) -> None:
        """Steps 5 and 6: the settings call, then the resize at the VM's create alone - a create resumed included -
        and only where its disk is smaller than asked, since Proxmox refuses to shrink one a person grew meanwhile."""
        client = self.host.client
        kept = kept_size(found, size)
        changes = settings_call(self.values, size, self.host.password, kept)
        repos = self.plan.repos
        self.step("settings")
        await self.call(client.edit_config, owned, lambda _: changes)
        if repos:
            await self.call(record.write, client, owned, lambda now: _with_repos(now, repos))
        self.step("settings", "done")
        disk = size.disk or overrides.capital("disk", str(self.values["vm_disk_size"]))
        has = proxmox.proxmox_disk_bytes(str(config.values.get(DISK) or ""))
        if (found is None or not found.provisioned) and "disk" not in kept and has < proxmox.proxmox_disk_bytes(disk):
            self.step("resize")
            task = await self.call(client.resize, owned, DISK, disk)
            self._warned(await self.call(client.follow, task, None, self.ctx.stopping))
            self.step("resize", "done")

    async def _start(self, owned: fleet.Owned) -> None:
        """Step 7's start: sent and followed to its end."""
        client = self.host.client
        self.step("start")
        task = await self.call(client.start, owned)
        self._warned(await self.call(client.follow, task, None, self.ctx.stopping))
        self.step("start", "done")

    async def _provisioned(self, size: Size) -> None:
        """Step 7's record write, once the VM started and its address was read: provisioned, and the size the run
        gave."""
        owned = self.owned
        assert owned is not None

        def change(now: record.Record | None) -> record.Record | None:
            if now is None:
                return None
            return dataclasses.replace(
                now,
                provisioned=True,
                cores=size.cores if size.cores is not None else now.cores,
                memory=size.memory or now.memory,
                disk=size.disk or now.disk,
            )

        await self.call(record.write, self.host.client, owned, change)

    async def _address(self) -> str:
        """Step 7's address: waited for from the guest agent on the VM's own card, then held against every running
        member's the fleet owns."""
        target = self.target
        assert target is not None
        if not target.mac:
            raise Failed(NO_MAC.format(nic=self.host.nic, name=self.name, vmid=target.vmid))
        self.step("address")
        await agent.wait_for_address(
            self.host.client, target.node, target.vmid, target.mac, agent.DEADLINE, self.ctx.cancelled
        )
        requested = await self.call(fleet.requested, self.host, (self.name,), change=False)
        [read] = requested.targets
        if not read.address:
            raise Failed(" ".join(requested.said) or f"{self.name} reported no address")
        self.target = read
        self.step("address", "done")
        self.emit("report", f"{self.name} is up at {read.address}")
        return read.address

    async def _key(self, address: str) -> None:
        """Step 8: the key taken at first use and filed under the identity, for a VM this run created or one resumed
        before its first configure; then its master made sure of."""
        import asyncio
        import time

        target = self.target
        assert target is not None
        [base] = await self.route.targets([target])
        stage = target.record.stage if target.record is not None else "cloning"
        self.step("key")
        if self.created or stage in ("cloning", "unfinished"):
            first = self.route.first_use(base)
            if first.first_use:
                end = time.monotonic() + SSHD
                while True:
                    result = await runner.run(self.ctx.job, first, ["true"], None, FIRST, dict(self.host.environ))
                    if isinstance(result, runner.Done) and result.status == 0:
                        break
                    if time.monotonic() > end:
                        why = getattr(result, "said", "") or "it did not answer"
                        raise Failed(f"{self.name} did not let this run take its SSH host key: {one_line(why, 300)}")
                    await asyncio.sleep(PAUSE)
                try:
                    key = hostkeys.taken(first.known_hosts, target.identity)
                    hostkeys.file(base.known_hosts, target.identity, key)
                except (hostkeys.Refused, hostkeys.Changed) as refused:
                    raise Failed(f"{self.name}'s SSH host key was not filed: {one_line(str(refused), None)}") from None
        master = await self.route.ensure(base)
        if master is not None and not master.ok:
            raise Failed(f"{self.name} at {address} could not be reached: {master.said}")
        self.step("key", "done")


def _with_repos(now: record.Record | None, repos: Sequence[str]) -> record.Record | None:
    """now with repos added to its own list, each once, in the order given after those it has; None for no change."""
    if now is None:
        return None
    have = tuple(now.repositories or ())
    merged = tuple(dict.fromkeys([*have, *repos]))
    return None if merged == have and now.repositories is not None else dataclasses.replace(now, repositories=merged)


def _has_cloud_init(host: fleet.Host, source: template.Compared) -> bool:
    """Whether the template a VM is cloned from has a cloud-init drive, which the settings call's user and keys reach a
    VM by: read once, before anything is cloned (the judgement does not hold it, E12's L4)."""
    try:
        config = host.client.config(source.node, source.template_id, host.nic)
    except (ProxmoxError, TransportError) as error:
        raise fleet.Refused(f"the template on {host.name} could not be read: {fleet.sentence(error)}") from None
    return template.cloud_init(config)


# ---------------------------------------------------------------- the run ----


async def provision(ctx: Context, host: fleet.Host, names: Sequence[str], plan: Plan) -> bool:
    """`deploy --only provision` on host: each VM of names made or resumed, side by side, every one said. True where
    every VM was provisioned (or left stopped, as asked); False where one failed, the others gone on. Refused (the
    route's, the fleet's) for the whole run, before anything is created."""
    import asyncio

    from ..runs import job as jobs

    requested = await asyncio.to_thread(fleet.requested, host, tuple(names), create=True, change=True)
    for line in requested.said:
        ctx.emit("report", None, line)
    route = await routes.settle(ctx, host)
    source = None
    if requested.new:
        values = {name: value.value for name, value in host.values.items()}
        keys = values["vm_ssh_public_keys"] or ()
        if not keys and host.password is None:
            raise fleet.Refused(UNREACHABLE)
        try:
            source = await asyncio.to_thread(
                template.compare, host.client, values, pool=host.pool, host_flag=host.host_flag
            )
        except (ProxmoxError, TransportError) as error:
            raise fleet.Refused(f"the template on {host.name} could not be read: {fleet.sentence(error)}") from None
        if not source.usable:
            raise fleet.Refused(f"{source.said()} No VM is cloned from it (card E13 builds and rebuilds the template).")
        if not await asyncio.to_thread(_has_cloud_init, host, source):
            raise fleet.Refused(NO_CLOUD_INIT.format(name=source.name, vmid=source.template_id))
    makings = [
        Making(ctx, host, route, plan, None, target.name, target.identity, target=target)
        for target in requested.targets
    ] + [Making(ctx, host, route, plan, source, name, jobs.stand_in()) for name in requested.new]
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(making.run()) for making in makings]
    return all(task.result() for task in tasks)
