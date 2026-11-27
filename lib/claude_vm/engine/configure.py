"""Configuring a machine, `deploy --only configure`, the frame (card G4; docs/architecture/engine.md §16.4): each VM is
reached, its system layer brought to its settings by layer 1's child (guest/layer1.py) running the system play
(guest/system.py), its user's real home read, and its record's stage written once every part asked for has run. Every
later part of a configure (layer 2's, card G9; the sign-ins, G12) is added here.

**The parts.** A configure asks for parts: today's six (schema/rules.py's `CONFIGURE_ROLES`), every one where `--tags`
names none, or those it names. The system play runs the three of layer 1 (`SYSTEM_PARTS`: `system.PARTS` but
`tailscale`, whose part installs the package and whose join is layer 2's); `claude_code`, `github_projects` and
`tailscale` are layer 2's, which G9 builds: until then a `--tags` that names one is refused before
the run, naming G9 (`unbuilt`), and a configure with no `--tags` runs the three, says by name that the other three were
not run, and leaves the VM `unfinished`. The record's stage becomes `finished` only where one configure ran all six
(`stage_after`), so not before G9; the converged digest is left empty, and so never written, until F28.

**The steps of one VM**, each an event of the run:

1. Read by a changing read (fleet.requested): a VM the fleet may not change refuses the run where it is named, and is
   left out and said where none is; each target carries its Owned. A VM whose record is not provisioned fails, naming
   the provision line that finishes it.
2. A stopped VM is said and left as it is, never waited on, unless the plan starts it (`start`, which only the whole
   chain, G10, gives): then it is started through its Owned, its task followed, and its address waited for from its
   guest agent.
3. Before anything connects to it: its identity's host key filed in the product's known-hosts file
   (layer1.key_filed), else the VM fails with the line that files it. Its Target is the route's (Route.targets, one
   VM a call, so that one refused fails that VM alone), its master made sure of (Route.ensure) and `true` run through
   the runner until it answers, for SSHD seconds at most: its sshd may still be coming up.
4. Where `--remove-key` names a key (`Plan.remove_key`), that key taken off the account by guest/keys.py's `remove`, a
   one-task play of layer 1's, before anything else: a key `vm_ssh_public_keys` lists, one this computer's ssh offers,
   or one on no line of the VM's fails the VM with why, nothing changed; then said, with the lines it took.
5. Layer 1: the play's variables made from the settings for the parts run (wiring.role_variables, the same parts as
   the child's `--tags`) and written in the run's directory through `system.variables`, held to every secret the run
   holds; then the child, every one of those secrets struck from what it says. A failed task fails the VM, said in
   this engine's words with the task named (cloud-init's own failure with this engine's lines), a VM layer 1 could not
   reach fails saying so, a child that ended otherwise fails with its last words (a play that did not load among
   them), and a run that ended well with no recap heard from the product's callback fails too: a callback of the same
   name beside the play or in a role was loaded in its place (F5).
6. The VM user's real home read from the password database through the runner (`getent passwd`), and the workspace
   made from it as every other reader makes it (sessions.projects_dir): the outcome G8's alias is written from, given
   only for a VM that succeeded.
7. The record written where the stage changed (record.write, under its Owned), else nothing is sent.

A step that fails fails its VM alone, and the others go on; a route that cannot be used stops the run (routes.Unusable).
Before the machines are reached side by side, the hop's shared connection is made sure of again (Route.ready). The
interpreter and sudo are named by their paths in what layer 1 generates; nothing here passes an option that could change
them, nor a test's connection.

Standard library, guest/'s modules and the engine's at import; asyncio inside the coroutines.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .. import sessions
from ..guest import keys, layer1, system, wiring
from ..pve.client import ProxmoxError
from ..runs.stages import Declared, Stage, Step
from ..schema import printer
from ..schema.printer import said
from ..schema.rules import CONFIGURE_ROLES
from ..schema.words import Line
from ..secret import Secret
from ..ssh import runner
from ..text import one_line
from ..vendors.transport import TransportError
from . import fleet, record, report, routes

if TYPE_CHECKING:
    from ..runs import job as jobs
    from ..runs.attached import Context

# The run's one stage and its steps, as the live view draws them: a run declares only what it runs. Layer 1's own
# events are lines of a machine's work, which name no stage.
STAGE = "configure"
STEPS = (
    Step("start", "start"),
    Step("reach", "reach"),
    Step("key", "key taken off"),
    Step("system", "system layer"),
    Step("home", "home"),
)
DECLARED = Declared("deploy", (Stage(STAGE, "configure", steps=STEPS),))
# The parts of a configure layer 2 runs, which card G9 builds: tailscale's package is layer 1's (system.PARTS), and is
# installed with its join, by G9, so a configure leaves it until then.
LAYER_2 = ("claude_code", "github_projects", "tailscale")
# The system play's parts a configure runs: every one but those whose part is layer 2's.
SYSTEM_PARTS = tuple(part for part in system.PARTS if part not in LAYER_2)
LAYER_2_CARD = "G9"
# The stage a VM's record names once one configure ran every part.
FINISHED = "finished"
# How long a machine's sshd is waited for, and the pause between tries.
SSHD = 180.0
PAUSE = 2.0
# One connection that runs `true`, or reads the password database: a few seconds' work.
QUICK = runner.Limits(seconds=30.0, silent=None)
# The task of the system play that fails with what cloud-init reported, said in this engine's words.
CLOUD_INIT_TASK = "Fail with what cloud-init reported"
# The play's own variable that turns the wait for cloud-init off, which a repaired VM's configure is given.
CLOUD_INIT_WAIT = "configure_wait_for_cloud_init"

NOT_PROVISIONED = "{name} is not provisioned yet, so nothing configures it: {provision} finishes it, then run {line}."
LEFT_STOPPED = (
    'VM "{name}" (VMID {vmid}) is stopped, so it was left as it is and not configured. Start it in Proxmox, or with'
    " {start}, then run {line} again."
)
NOT_RUN = (
    "{name}: {parts} were not run, which card {card} builds, so {name} stays unfinished until a configure runs every"
    " part."
)
CLOUD_INIT = (
    "{name}: cloud-init's first boot ended in an error, so the VM may be half set up; /var/log/cloud-init-output.log on"
    " it says why. Building it again is the sure way past a failure that will not repeat (a mirror, a corrupted"
    " download): {destroy}, then {provision} and {line}. Or repair it by hand, then run {line} with --set"
    " {wait}=false."
)
TASK_FAILED = '{name}: the task "{task}" failed: {message}'
UNREACHABLE = "{name} could not be reached by layer 1: {message}"
ENDED = "{name}: layer 1 ended with exit status {status} before any task failed: {said}"
NO_RECAP = (
    "{name}: layer 1 ended well but no event of the product's own callback was heard, so what it did is not known: a"
    " callback plugin named aw_events beside the play or in a role was loaded in its place. Remove it, then run {line}"
    " again."
)
NO_HOME = "{name}: the home of its user {user} could not be read: {why}"
KEY_TAKEN_OFF = "{name}: the key {fingerprint} was taken off its account ({lines} {noun} of ~/.ssh/authorized_keys)."


class Failed(Exception):
    """One VM's step failed: the sentence that says why. The VM is left as the step found it, for the next run. own: a
    failure of the VM's own (a layer-1 task that failed, or could not reach it), which stays the VM's failure though the
    run is stopping; any other ends the VM stopped once the run is."""

    def __init__(self, sentence: str, *, own: bool = False) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.own = own


# ---------------------------------------------------------------- what a run asks ----


@dataclasses.dataclass(frozen=True)
class Plan:
    """What a configure asks: its parts, in CONFIGURE_ROLES' order (every one where --tags named none), whether a
    stopped VM is started first (the whole chain's --start; deploy --only configure never gives it), and the
    fingerprint of the key --remove-key takes off each VM's account, "" for none."""

    parts: tuple[str, ...] = CONFIGURE_ROLES
    start: bool = False
    remove_key: str = ""

    @classmethod
    def read(cls, tags: object = None, start: object = False, remove_key: object = None) -> Plan:
        """The plan of --tags' value as the one check holds it (roles separated by single commas, each one of
        CONFIGURE_ROLES): every part where it names none; and --remove-key's fingerprint, held (guest/keys.py)."""
        named = [part for part in str(tags or "").split(",") if part]
        unknown = [part for part in named if part not in CONFIGURE_ROLES]
        if unknown:
            raise ValueError(f"--tags may name only {', '.join(CONFIGURE_ROLES)}")
        parts = tuple(part for part in CONFIGURE_ROLES if part in named) if named else CONFIGURE_ROLES
        removing = keys.held(remove_key) if remove_key not in (None, "") else ""
        return cls(parts=parts, start=start is True, remove_key=removing)

    @property
    def system_parts(self) -> tuple[str, ...]:
        """The parts asked that the system play runs, in its order."""
        return tuple(part for part in SYSTEM_PARTS if part in self.parts)

    @property
    def unrun(self) -> tuple[str, ...]:
        """The parts asked that nothing runs yet: layer 2's, until G9."""
        return tuple(part for part in self.parts if part in LAYER_2)


def unbuilt(tags: object) -> str | None:
    """The sentence that refuses a --tags naming a part of layer 2's, which no card has built yet, naming G9; None where
    it names none."""
    named = [part for part in str(tags or "").split(",") if part in LAYER_2]
    if not named:
        return None
    return said(
        "error: --tags {parts} names a part of a VM's second layer, which card {card} builds: until then, {line}"
        " configures {system}",
        {
            "parts": ",".join(named),
            "card": LAYER_2_CARD,
            "line": Line("deploy", {"only": STAGE, "tags": ",".join(SYSTEM_PARTS)}, host_flag=""),
            "system": ", ".join(SYSTEM_PARTS),
        },
    )


def stage_after(ran: Sequence[str]) -> str | None:
    """The stage a VM's record takes once a configure ran the parts ran: finished where it ran every one of today's six
    parts in that one configure, else None, the stage left as it is."""
    return FINISHED if set(CONFIGURE_ROLES) <= set(ran) else None


def home_of(lines: Sequence[str], user: str) -> str:
    """The home directory of user, read from getent passwd's line for it: the sixth field, an absolute path."""
    for line in lines:
        fields = line.split(":")
        if len(fields) >= 7 and fields[0] == user and fields[5].startswith("/"):
            return fields[5]
    raise ValueError("getent gave no line for it with an absolute home")


@dataclasses.dataclass(frozen=True)
class Configured:
    """One VM's configure as it ended: its name and identity, its outcome (succeeded, failed or left stopped), the
    stage its record names, and, only where it succeeded, its user's real home and its workspace, which G8's alias is
    written from."""

    name: str
    identity: str
    outcome: str
    stage: str = ""
    home: str = ""
    workspace: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in ("succeeded", "left stopped")


class Failures:
    """Layer 1's failed tasks, by machine, as the run's events carry them: the last failed task's name and message of
    each machine's, heard as they come. One listener a run; it never raises."""

    def __init__(self) -> None:
        self.found: dict[str, tuple[str, str]] = {}

    def __call__(self, event: jobs.Event) -> None:
        data = event.data
        if event.kind != "step" or data.get("stage") is not None or event.machine is None:
            return
        if data.get("status") in ("failed", "unreachable") and not data.get("ignored") and "item" not in data:
            self.found[event.machine] = (str(data.get("task") or ""), str(data.get("message") or ""))


# ---------------------------------------------------------------- one VM ----


@dataclasses.dataclass(eq=False)
class Configuring:
    """One VM's configure in one run: the run's context, the host, the route, what the run asks, the target as the
    run's read found it, and the failures heard."""

    ctx: Context
    host: fleet.Host
    route: routes.Route
    plan: Plan
    target: fleet.Target
    failures: Failures
    ended_as: str = "configured"
    stage: str = ""
    home: str = ""
    workspace: str = ""

    @property
    def name(self) -> str:
        return self.target.name

    @property
    def key(self) -> str:
        return self.target.identity

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

    @property
    def secrets(self) -> tuple[Secret, ...]:
        """Every secret the run holds: each host's token's, and the VMs' console password."""
        held = tuple(self.host.tokens.values())
        return held + ((self.host.password,) if self.host.password is not None else ())

    def line(self, **more: object) -> Line:
        return Line("deploy", {"names": [self.name], "only": STAGE, **more}, host_flag=self.host.host_flag)

    def provision_line(self, **more: object) -> Line:
        return Line("deploy", {"names": [self.name], "only": "provision", **more}, host_flag=self.host.host_flag)

    async def call(self, function, *args, **kwargs):
        import asyncio

        return await asyncio.to_thread(function, *args, **kwargs)

    def outcome(self, outcome: str) -> Configured:
        kept = outcome == "succeeded"
        home, workspace = (self.home, self.workspace) if kept else ("", "")
        return Configured(self.name, self.key, outcome, self.stage, home, workspace)

    # ------------------------------------------------------------ the whole ----

    async def run(self) -> Configured:
        """The VM configured, and said. A cancellation, and a route that cannot be used, end the run; a stop that ends a
        wait of the run's own (the guest agent's), which raises asyncio.CancelledError, ends the VM stopped, its stage
        kept, as a step the stop ended does."""
        import asyncio

        self.emit("stage", STAGE, name=self.name)
        try:
            await self._run()
        except asyncio.CancelledError:
            if not self.ctx.cancelled.is_set():
                raise
            self.emit("end", "stopped", outcome="cancelled")
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                # The run itself is cancelled (the grace's end, a second Ctrl-C): its end goes on, the VM's said.
                raise
            return self.outcome("cancelled")
        except Failed as failed:
            why = one_line(failed.sentence, None)
            if self.ctx.cancelled.is_set() and not failed.own:
                self.emit("end", "stopped", outcome="cancelled")
                return self.outcome("cancelled")
            self.emit("stage", STAGE, outcome="failed", why=why)
            self.emit("end", why, outcome="failed")
            return self.outcome("failed")
        if self.ended_as == "left stopped":
            self.emit("stage", STAGE, outcome="skipped")
            self.emit("end", self.ended_as, outcome="succeeded")
            return self.outcome("left stopped")
        self.emit("stage", STAGE, outcome="done")
        self.emit("end", self.ended_as, outcome="succeeded")
        return self.outcome("succeeded")

    async def _run(self) -> None:
        try:
            await self._configure()
        except routes.Unusable:
            # The route cannot be used: the run's stop, never one VM's.
            raise
        except routes.Refused as refused:
            raise Failed(f"{self.name}: {refused.sentence}") from None
        except (layer1.Refused, keys.Refused) as refused:
            raise Failed(f"{self.name}: {one_line(str(refused), None)}") from None
        except keys.Failed as failed:
            raise Failed(f"{self.name}: {one_line(str(failed), None)}", own=True) from None
        except (ProxmoxError, TransportError, fleet.Refused, record.Unreadable, record.TooLong) as error:
            raise Failed(f"{self.name}: {fleet.sentence(error)}") from None
        except (wiring.Refused, ValueError) as error:
            raise Failed(f"{self.name}: {one_line(str(error), None)}") from None

    async def _configure(self) -> None:
        target = self.target
        found = target.record
        self.stage = found.stage if found is not None else ""
        if found is None or not found.provisioned or found.stage == "cloning":
            filled = {"name": self.name, "provision": self.provision_line(), "line": self.line()}
            raise Failed(said(NOT_PROVISIONED, filled))
        if not target.running:
            if not self.plan.start:
                start = self.provision_line(start=True)
                filled = {"name": self.name, "vmid": target.vmid, "start": start, "line": self.line()}
                left = printer.line(start.verb, start.values, refused=False, host_flag=start.host_flag)
                report.say(self.ctx, self.key, "vm", said(LEFT_STOPPED, filled), left)
                self.ended_as = "left stopped"
                return
            await self._start()
        # Before anything connects to it: its key filed under its identity, which the route's Target is checked
        # against (runner.KNOWN_HOSTS), or the line that files it.
        layer1.key_filed(runner.KNOWN_HOSTS, target.identity, self.name, self.host.host_flag)
        reached = await self._reach()
        await self._remove_key(reached)
        ran = await self._system(reached)
        await self._home(reached)
        stage = stage_after(ran)
        if self.plan.unrun:
            unrun = NOT_RUN.format(name=self.name, parts=_listed(self.plan.unrun), card=LAYER_2_CARD)
            report.say(self.ctx, self.key, "configure", unrun, "")
        if stage is not None and stage != self.stage:
            await self._record(stage)
        self.ended_as = f"configured, {self.stage}" if self.stage else "configured"

    # ------------------------------------------------------------ step 2: a stopped VM started ----

    async def _start(self) -> None:
        from ..pve import agent

        target = self.target
        owned = target.owned
        assert owned is not None
        client = self.host.client
        self.step("start")
        task = await self.call(client.start, owned, self.ctx.stopping)
        await self.call(client.follow, task, None, self.ctx.stopping)
        if target.mac:
            cancelled = self.ctx.cancelled
            await agent.wait_for_address(client, target.node, target.vmid, target.mac, agent.DEADLINE, cancelled)
        requested = await self.call(fleet.requested, self.host, (self.name,), change=True)
        [read] = requested.targets
        if not read.address:
            raise Failed(" ".join(requested.said) or f"{self.name} reported no address")
        self.target = read
        self.step("start", "done")

    # ------------------------------------------------------------ step 3: reached ----

    async def _reach(self) -> runner.Target:
        """The VM's Target, from the route, its master made sure of and `true` answered over it."""
        import asyncio
        import time

        self.step("reach")
        [reached] = await self.route.targets([self.target])
        end = time.monotonic() + SSHD
        while True:
            master = await self.route.ensure(reached)
            why = master.said if master is not None and not master.ok else ""
            if not why:
                result = await runner.run(self.ctx.job, reached, ["true"], None, QUICK, dict(self.host.environ))
                if isinstance(result, runner.Done) and result.status == 0:
                    break
                why = getattr(result, "said", "") or "it did not answer"
            if time.monotonic() > end:
                raise Failed(f"{self.name} at {self.target.address} could not be reached: {one_line(why, 300)}")
            await asyncio.sleep(PAUSE)
        self.step("reach", "done")
        return reached

    # ------------------------------------------------------------ step 4: a key taken off ----

    async def _remove_key(self, reached: runner.Target) -> None:
        """--remove-key's key taken off the VM's account (guest/keys.py), before its system layer: a key of a computer
        that is lost is the first thing a configure takes away."""
        if not self.plan.remove_key:
            return
        self.step("key")
        removed = await keys.remove(
            self.ctx.job,
            reached,
            self.plan.remove_key,
            name=self.name,
            host_flag=self.host.host_flag,
            env=dict(self.host.environ),
            listed=tuple(self.values.get("vm_ssh_public_keys") or ()),
            redacted=self.secrets,
        )
        noun = "line" if removed.lines == 1 else "lines"
        filled = {"name": self.name, "fingerprint": removed.fingerprint, "lines": removed.lines, "noun": noun}
        report.say(self.ctx, self.key, "configure", KEY_TAKEN_OFF.format(**filled), "")
        self.step("key", "done")

    # ------------------------------------------------------------ step 5: layer 1 ----

    async def _system(self, reached: runner.Target) -> tuple[str, ...]:
        """The system play run for the parts asked that it runs: those parts, which ran."""
        from ..runs import rundir

        parts = self.plan.system_parts
        if not parts:
            return ()
        self.step("system")
        settings = self.host.values
        document = wiring.role_variables(settings, self.name, parts)
        path = rundir.path_for(self.ctx.job, f"variables-{self.key}.json")
        written = system.variables(path, document, secrets=self.secrets)
        # Every secret the run holds struck from what the child says: the job's redaction list, which every event of
        # the run is struck of.
        self.ctx.job.redact(*self.secrets)
        ended = await layer1.run(
            self.ctx.job,
            reached,
            system.PLAY,
            written,
            name=self.name,
            host_flag=self.host.host_flag,
            env=dict(self.host.environ),
            tags=parts,
        )
        sentence = self._failed(ended)
        if sentence and self.ctx.cancelled.is_set() and not self._task_failed(ended):
            # The run's own Ctrl-C ended the child (ansible-core's "User interrupted execution", exit 99) or the grace's
            # end killed it: its exit status alone fails nothing. The step is left to the VM's cancelled end, which the
            # model draws stopped.
            raise Failed(sentence)
        if sentence:
            self.step("system", "failed", sentence)
            # A task that failed is the VM's failure, the run stopping or not.
            raise Failed(sentence, own=True)
        self.step("system", "done")
        return parts

    def _task_failed(self, ended: layer1.Ended) -> bool:
        """Whether layer 1 said a task of this VM failed or could not reach it: a failure of its own, never its exit
        status alone."""
        counts = ended.counts
        return bool(counts.unreachable or counts.failed or self.key in self.failures.found)

    def _failed(self, ended: layer1.Ended) -> str:
        """Why layer 1's run failed the VM, in this engine's words, or "" where it went well."""
        counts = ended.counts
        task, message = self.failures.found.get(self.key, ("", ""))
        message = one_line(message.replace(self.key, self.name), 600) if message else ""
        if counts.unreachable:
            return UNREACHABLE.format(name=self.name, message=message or _last(ended.said))
        if task == CLOUD_INIT_TASK:
            return said(
                CLOUD_INIT,
                {
                    "name": self.name,
                    "destroy": Line("destroy", {"names": [self.name]}, host_flag=self.host.host_flag),
                    "provision": self.provision_line(),
                    "line": self.line(),
                    "wait": CLOUD_INIT_WAIT,
                },
            )
        if task or counts.failed:
            return TASK_FAILED.format(name=self.name, task=task or "of layer 1", message=message or _last(ended.said))
        if ended.status != 0:
            return ENDED.format(name=self.name, status=ended.status, said=_last(ended.said))
        if not counts.recap:
            return said(NO_RECAP, {"name": self.name, "line": self.line()})
        return ""

    # ------------------------------------------------------------ step 6: the home ----

    async def _home(self, reached: runner.Target) -> None:
        """The VM user's real home, read from the password database, and the workspace made from it."""
        user = self.target.user
        self.step("home")
        asked = ["getent", "passwd", user]
        result = await runner.run(self.ctx.job, reached, asked, None, QUICK, dict(self.host.environ))
        if not isinstance(result, runner.Done) or result.status != 0:
            why = one_line(getattr(result, "said", "") or "getent did not answer", 300)
            raise Failed(NO_HOME.format(name=self.name, user=user, why=why))
        try:
            self.home = home_of(result.lines, user)
        except ValueError as error:
            raise Failed(NO_HOME.format(name=self.name, user=user, why=error)) from None
        self.workspace = sessions.projects_dir(self.values.get("vm_projects_dir"), self.home)
        self.step("home", "done")

    # ------------------------------------------------------------ step 7: the record ----

    async def _record(self, stage: str) -> None:
        owned = self.target.owned
        assert owned is not None

        def change(now: record.Record | None) -> record.Record | None:
            if now is None or now.stage == stage:
                return None
            # The converged digest stays empty, and so unwritten, until F28 makes one.
            return dataclasses.replace(now, stage=stage)

        await self.call(record.write, self.host.client, owned, change)
        self.stage = stage


def _listed(parts: Sequence[str]) -> str:
    """parts as a sentence names them: a, b and c."""
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _last(text: str) -> str:
    """The last line of what layer 1's child printed that says something, as one line."""
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    return one_line(lines[-1], 600) if lines else "it said nothing"


# ---------------------------------------------------------------- the run ----


async def configure(ctx: Context, host: fleet.Host, names: Sequence[str], plan: Plan) -> tuple[Configured, ...]:
    """`deploy --only configure` on host: each VM of names, or every VM of the pool this fleet may change where none is
    named, configured side by side, every one said: each one's outcome. Refused (the route's, the fleet's) for the
    whole run before anything reaches a VM."""
    import asyncio

    requested = await asyncio.to_thread(fleet.requested, host, tuple(names), change=True)
    for line in requested.said:
        report.say(ctx, None, "fleet", line, "")
    route = await routes.settle(ctx, host)
    failures = Failures()
    ctx.job.listen(failures)
    configurings = [Configuring(ctx, host, route, plan, target, failures) for target in requested.targets]
    if not configurings:
        return ()
    await route.ready()
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(each.run()) for each in configurings]
    return tuple(task.result() for task in tasks)
