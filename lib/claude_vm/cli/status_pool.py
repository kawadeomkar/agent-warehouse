"""aw status's reader of the fleet: the pool's members and each one's record, where today's status reads a tag
(docs/architecture/engine.md §16.4).

aw status's entry (cli/status.py) hands `Pool` to today's engine (lib/claude_vm/status/engine.py's run) as the reader of
the fleet, beside status_inputs' loader, as that engine's `Tagged` is today's: so tools/status keeps reading its
tag-discovered fleet, and prints what it prints today. The engine's GET-only client makes every read (GETs, no redirect
followed), and the model's functions make the report as they stand; what this reader says beyond them is a function of
its own, added beside the old output, never a change to an old function's text.

**What is the fleet's.** The pool's members (GET /pools?poolid=, type qemu), each one's configuration read once, before
the VMs are drawn, and judged by pve/ownership.py's one rule (`Member.read`, `refusal`), with the other members that
carry its UUID as its twins, so that status and every changing verb never part. Only a member the rule passes is a VM
of the report; one it refuses is not this fleet's and is said in one line: one with no record of this engine's in a
fixed sentence (this reader reads no tag), with the destroy line that deletes it under destroy_allow_unrecorded, any
other in the rule's own sentence. A member whose configuration the token may not read is said as not read, never as
failed. A member on a node that is down cannot be read, so it is never judged and never a VM of the report: one line
says so. Every other member's configuration is read, a template's too, and the twins are found among them all by
pve/ownership.py's twins, the engine's rule. A template is never a VM of the report.

**The record** gives what today's tags gave: whether its first deploy finished (its stage), its own list of
repositories, whether it joined the tailnet before, and whether it was made with a size of its own (engine/record.py's
`Record.sized`), against which its size is never drift. Drift is the three kinds the design names: an observed
difference (today's), a deep diff (deploy --check's, card G20), and an unapplied change, whose digests differ, shown as
"not known" while the record keeps no digest to compare (card F28). The template is judged by pve/template.py's
`judge`, on the engine's record, never today's notes line.

**Lines.** Every command a line names says aw: the model's and Tailscale's through the program they are given, and
each of this module's through the printer (schema/printer.py), a whole line or a sentence `said` writes. New lines: a
member not this fleet's; a VM whose record names a job this computer has no record of, which a run on another computer
left; a job of this computer's that ended without its outcome (lost); a VM this computer has filed no host key for,
whose SSH sections are not read, since status takes none, and which the first run that reaches it from here files; the
node's other VMs not read under proxmox_audit_scope: pool. A storage's figure of its disks says what it counts where it
counts less than every VM's.

**The other VMs' disks.** Under proxmox_audit_scope: node a storage lists a VM's disk only where the token holds
VM.Config.Disk on it, which the grant on /vms (VM.Audit) does not give: so of each VM outside the pool its configuration
is read (VM.Audit gives that), after every read of the fleet's and only where the storage or capacity section is asked
for, and of it only each disk's storage and size are kept, made an entry of that storage's content, so that the model
counts the storage's figure as it does today. The figure is then a floor: an unused disk names no size, and a volume no
configuration names (a deleted VM's, a snapshot's state) is never seen. Under pool they are not read at all.

It writes nothing: the engine writes its cache, the run's log and ssh's sockets. Standard library, pve/, runs/job.py,
schema/, ssh/hostkeys.py and today's text.py: none of claude_vm.engine, today's status, pydantic, Ansible
or the MCP SDK (card G15's boundary, tests/unit/test_aw_cli_imports.py).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Final

from claude_vm.pve import answers, ownership, template
from claude_vm.pve import record as block
from claude_vm.runs import job as jobs
from claude_vm.schema import printer
from claude_vm.schema.patterns import full
from claude_vm.schema.words import Line
from claude_vm.ssh import hostkeys
from claude_vm.text import one_line

# The levels of an item, worst first, and the exit each gives, as today's model ranks them (status/model.py's LEVELS and
# status_report's exit): held equal by tests/unit/test_aw_status_pool.py.
LEVELS: Final = ("unread", "problem", "todo")
EXIT_OF: Final = {"unread": 2, "problem": 1}
# A VM's disks in its configuration: each bus's numbered slots, and the EFI and TPM disks.
DISK_KEY: Final = re.compile(r"(?:scsi|virtio|sata|ide)[0-9]+|efidisk0|tpmstate0")
# A disk's storage and size in its configuration's value (local-lvm:vm-9013-disk-0,size=32G): its volume's storage, the
# first item's part before its colon, and its size= in K, M, G or T.
VOLUME: Final = re.compile(r"([^:,=]+):[^,]*")
SIZE: Final = re.compile(r"(?:^|,)size=([0-9]+(?:\.[0-9]+)?)([KMGT])(?:,|\Z)")
# The sections whose rows count a storage's disks or the node's guests: only they read the other VMs' configurations.
COUNTING: Final = frozenset({"storage", "capacity"})
# The key the document gains beside today's read_at and now: a control VM's age of what it shows, here the run's own
# time, since nothing is older than the run.
KNOWN_AT: Final = "known_at"
# What a VM's row says of an unapplied change while its record keeps no digest of what it converged to (card F28).
NOT_KNOWN: Final = "not known"
# What a storage's figure counts, where it counts less than every VM's.
FLOOR: Final = "a floor: the VMs outside the pool are counted by their configurations, so a disk none names is not"
POOL_ALONE: Final = "its disks' figure counts the pool's guests alone (proxmox_audit_scope: pool)"
NOTHING: Final = {"count": 0, "vcpus": 0, "mem": 0, "running": 0, "nodes": []}


@dataclass
class _Read:
    """What was read of one member: its entry, its record's fields where the rule passed it, and why not."""

    entry: dict
    fields: Mapping[str, Any] = field(default_factory=dict)
    refused: str = ""


def _plain(text: object, limit: int = 120) -> str:
    return one_line(str(text or ""), limit)


def _item(level: str, vm: str, text: str, command: str = "") -> dict:
    """An item of the report's attention, in the model's shape (status/model.py's _item)."""
    return {"level": level, "vm": vm, "source": "status", "subject": vm, "text": text, "command": command}


def _data(result: Mapping[str, Any]) -> Any:
    body = result.get("json") if isinstance(result, Mapping) else None
    return body.get("data") if isinstance(body, Mapping) else None


def disks(config: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Each disk of a VM's configuration on a storage, as (storage, size in bytes): its disk keys alone, a CD-ROM's and
    one that names no size none, and nothing else of the configuration."""
    found = []
    for key, value in config.items():
        text = str(value or "")
        volume, size = VOLUME.match(text), SIZE.search(text)
        if not DISK_KEY.fullmatch(str(key)) or "media=cdrom" in text or volume is None or size is None:
            continue
        found.append((volume[1], int(float(size[1]) * 1024 ** ("KMGT".index(size[2]) + 1))))
    return found


def _not_templates(entries: Sequence[dict]) -> list[dict]:
    """The entries that are no template, as today's not_templates has it."""
    return [entry for entry in entries if not int(entry.get("template") or 0)]


class Pool:
    """The reader of the fleet aw status hands today's engine: see the module's docstring. given is what
    cli/status_inputs.py's load gives under "pool"."""

    def __init__(self, given: Mapping[str, Any]) -> None:
        self.pool = str(given["pool"])
        self.scope = str(given["scope"])
        self.base: template.Base = given["base"]
        self.want: Mapping[str, str] = given["want"]
        self.custom = bool(given["custom"])
        self.known_hosts = str(given["known_hosts"])
        self.jobs = str(given["jobs"])
        self.host = str(given["host"])
        self.host_flag = str(given["host_flag"])
        self.read: dict[int, _Read] = {}
        self.configs: dict[int, dict] = {}
        self.listing: list[dict] = []
        self.members_of: set[int] = set()
        self.said: list[dict] = []
        self.counted_outside = False

    # --- which VMs are the fleet's -------------------------------------------------------------------------------
    def members(self, run: Any, api: Any, data: Sequence[Any], waited: Any) -> list[dict] | None:
        """The pool's members the one rule passes, each as the listing has it; each it refuses said. None, said by
        run.unread, where the pool's members cannot be read."""
        self.listing = [one for one in data if isinstance(one, dict)]
        asked = {"kind": "pool", "path": f"pools?poolid={self.pool}&type=qemu"}
        answer = waited(api.later(asked))
        listed = _data(answer)
        pool = listed[0] if isinstance(listed, list) and listed and isinstance(listed[0], Mapping) else None
        if answer.get("status") != 200 or pool is None or not isinstance(pool.get("members"), list):
            run.unread(
                f"Proxmox did not list the members of pool {self.pool}: {_plain(answer.get('msg')) or 'no answer'}",
                printer.said(
                    "the fleet's VMs are its members, and init makes it and grants the token's role on it: {init}",
                    {"init": Line("init", {"host": self.host})},
                ),
            )
            return None
        members = [one for one in pool["members"] if isinstance(one, dict) and one.get("type", "qemu") == "qemu"]
        self.members_of = {one.get("vmid") for one in members}
        by_vmid = {one.get("vmid"): one for one in self.listing}
        entries = {one.get("vmid"): {**one, **by_vmid.get(one.get("vmid"), {})} for one in members}
        # A member on a node that is down cannot be read, so it is never judged, and is never a VM of the report: one
        # line says so. Every other member's configuration is read, a template's too, so that the twins are found among
        # them all as the engine finds them (pve/ownership.py's twins); a template is never a VM of the report.
        readable = {}
        for vmid, entry in entries.items():
            if entry.get("status", "unknown") != "unknown":
                readable[vmid] = entry
                continue
            self.said.append(
                _item(
                    "unread",
                    _plain(entry.get("name") or f"VMID {vmid}"),
                    f"it is a member of pool {self.pool} on a node that is down ({_plain(entry.get('node'))}): not"
                    " read, so not known to be this fleet's",
                )
            )
        asks = {
            vmid: api.later({"kind": "config", "vmid": vmid, "path": f"nodes/{entry.get('node')}/qemu/{vmid}/config"})
            for vmid, entry in readable.items()
        }
        read: dict[int, answers.VmConfig] = {}
        for vmid, asked_config in asks.items():
            result = waited(asked_config)
            self.configs[vmid] = result
            entry = readable[vmid]
            name = _plain(entry.get("name") or f"VMID {vmid}")
            # A template's own read is the template section's, which says what of it could not be read.
            said = not entry.get("template")
            if result.get("status") != 200:
                if said:
                    self.said.append(
                        _item(
                            "unread",
                            name,
                            f"it is a member of pool {self.pool} whose configuration the token may not read"
                            f" ({_plain(result.get('msg')) or 'no answer'}): not read",
                        )
                    )
                continue
            try:
                read[vmid] = answers.config(_data(result), str(entry.get("node")), int(vmid), run.fleet["nic"])
            except (answers.ProxmoxError, TypeError, ValueError) as error:
                if said:
                    why = f"its configuration could not be read: {_plain(error)}: not read"
                    self.said.append(_item("unread", name, why))
        twins = ownership.twins(read.values())
        owned = []
        for vmid, config in read.items():
            entry = readable[vmid]
            if entry.get("template") or config.template:
                continue
            member = ownership.Member.read(config, twins=twins.get(vmid, ()))
            why = ownership.refusal(member)
            if why:
                self.said.append(self._not_ours(entry, member, why))
                continue
            self.read[vmid] = _Read(entry, block.parse(config.description) or {})
            owned.append(entry)
        return _not_templates(sorted(owned, key=lambda one: one.get("vmid") or 0))

    def _not_ours(self, entry: Mapping[str, Any], member: ownership.Member, why: str) -> dict:
        """The line of a member the rule refuses: one with no record in a fixed sentence and the line that deletes it,
        any other in the rule's own words."""
        name = _plain(entry.get("name") or f"VMID {member.vmid}")
        if member.recorded or member.unread or member.template:
            return _item("problem", name, _plain(why, 400))
        text = (
            f"it is a member of pool {self.pool} with no record of this engine's in its notes, so it is not this"
            " fleet's: it is never changed"
        )
        if not entry.get("name"):
            return _item("todo", name, text)
        # The printer's whole line, never a sentence's, which would mask the run's --set as a person's free text.
        deletes = printer.line(
            "destroy",
            {"names": [entry["name"]], "confirm": entry["name"], "set": ["destroy_allow_unrecorded=true"]},
            refused=False,
            host_flag=self.host_flag,
        )
        return _item("todo", name, f"{text}; the line beside this deletes it all the same", deletes)

    def ready(self, item: Mapping[str, Any]) -> Future | None:
        """A VM's configuration this reader read already, as a finished future in the engine's shape."""
        if item.get("kind") != "config" or item.get("vmid") not in self.configs:
            return None
        done: Future = Future()
        done.set_result({**self.configs[item["vmid"]], "item": item})
        return done

    def later(self, run: Any, requested: Sequence[Mapping[str, Any]]) -> list[dict]:
        """Under proxmox_audit_scope: node, where a section counts them, each VM outside the pool's configuration, its
        disks' storages and sizes being all that is kept of it (contents)."""
        if self.scope != "node" or not COUNTING & set(run.sections):
            return []
        return [
            {
                "kind": "outside",
                "vmid": one.get("vmid"),
                "node": one.get("node"),
                "path": f"nodes/{one.get('node')}/qemu/{one.get('vmid')}/config",
            }
            for one in self.listing
            if one.get("type", "qemu") == "qemu"
            and not one.get("template")
            and one.get("vmid") not in self.members_of
            and one.get("status", "unknown") != "unknown"
        ]

    def contents(self, run: Any, contents: list[dict], results: Sequence[Mapping[str, Any]]) -> list[dict]:
        """Each storage's disks, with each VM outside the pool's that the storage did not list, made from its
        configuration: a volume of that storage, its VMID and its size, and nothing else."""
        outside: dict[tuple[str, str], list[dict]] = {}
        for result in results:
            item = result.get("item") if isinstance(result, Mapping) else None
            if not isinstance(item, Mapping) or item.get("kind") != "outside" or result.get("status") != 200:
                continue
            config = _data(result)
            for storage, size in disks(config if isinstance(config, Mapping) else {}):
                outside.setdefault((str(item["node"]), storage), []).append(
                    {"volid": f"{storage}:", "vmid": item["vmid"], "size": size, "content": "images"}
                )
        if not outside:
            return contents
        made = []
        for one in contents:
            where = (str(one["item"].get("node")), str(one["item"].get("storage")))
            listed = _data(one)
            listed = list(listed) if isinstance(listed, list) else []
            seen = {volume.get("vmid") for volume in listed if isinstance(volume, Mapping)}
            more = [volume for volume in outside.get(where, []) if volume["vmid"] not in seen]
            if more and one.get("status") == 200:
                self.counted_outside = True
                one = {**one, "json": {"data": listed + more}}
            made.append(one)
        return made

    # --- what a VM is -----------------------------------------------------------------------------------------------
    def _fields(self, entry: Mapping[str, Any]) -> Mapping[str, Any]:
        found = self.read.get(entry.get("vmid"))  # type: ignore[arg-type]
        return found.fields if found else {}

    def joined(self, run: Any, entry: Mapping[str, Any]) -> bool:
        """Whether the VM joined the tailnet before: its record's joined names tailscale."""
        return "tailscale" in tuple(self._fields(entry).get("joined") or ())

    def unkeyed(self, run: Any, name: str, how: Mapping[str, Any], key_alias: str) -> tuple[str, str]:
        """Why the VM's SSH sections are not read, and the way on, where this computer has filed no host key for it:
        status takes none, so it is not dialled."""
        known = str(how.get("known_hosts") or self.known_hosts)
        try:
            filed = bool(key_alias) and bool(hostkeys.kept(known, key_alias))
        except (OSError, ValueError):
            filed = False
        if filed:
            return "", ""
        # The way on is the printer's whole line, since a sentence around it would be cut where a report's fields are.
        files = printer.line("deploy", {"names": [name], "only": "provision"}, refused=False, host_flag=self.host_flag)
        return (
            f"no host key of {_plain(name)} is filed here and status takes none, so its SSH sections are not read; the"
            " line beside this, the first run from here that reaches it, files it",
            files,
        )

    def wanted(self, run: Any, vm_settings: Mapping[str, Any], entry: Mapping[str, Any], config: Any) -> dict:
        """What the settings ask of the VM, its size its own where its record says it was made with one."""
        fields = self._fields(entry)
        sized = any(fields.get(part) for part in ("cores", "memory", "disk"))
        return {**vm_settings["drift"], "sized": sized}

    # --- the fleet and the report --------------------------------------------------------------------------------
    def fleet(self, run: Any, fleet: dict, results: Sequence[Mapping[str, Any]]) -> dict:
        """The fleet as the model made it, each VM's row from its record: its first deploy, its own repositories and
        its unapplied change; nothing outside the pool said as outside it, since it is the pool that is read; under
        proxmox_audit_scope: pool, the other VMs present and empty; each storage's figure said where it counts less;
        and the template as pve/template.py judges it."""
        if "vms" not in fleet:
            return fleet
        fleet = dict(fleet)
        rows = []
        for vm in fleet["vms"]:
            row = dict(vm)
            fields = self._fields(row)
            if fields:
                row["unfinished"] = fields.get("stage") != "finished"
                repositories = fields.get("repositories")
                row["own_repos"] = list(repositories) if repositories is not None else None
            row["unapplied"] = NOT_KNOWN
            rows.append(row)
        fleet["vms"] = rows
        fleet["pool_outside"], fleet["pool_read"] = [], True
        counted = POOL_ALONE if self.scope == "pool" else FLOOR if self.counted_outside else ""
        if self.scope == "pool":
            fleet["others"] = dict(NOTHING)
        if counted:
            fleet["storages"] = [{**one, "counted": counted} for one in fleet.get("storages") or []]
        if results:
            fleet["template"] = self._template(run, dict(fleet.get("template") or {}), results)
        return fleet

    def _template(self, run: Any, shown: dict, results: Sequence[Mapping[str, Any]]) -> dict:
        """The template's row, its drift pve/template.py's judgement of it on the engine's record: what differs, or
        nothing where it is as its settings would build it or is refused, which its own line says."""
        if shown.get("missing") or self.custom:
            return shown
        read = next((one for one in results if one.get("item", {}).get("kind") == "template"), None)
        entry = next((one for one in self.listing if one.get("vmid") == self.base.template_id), None)
        if read is None or entry is None or read.get("status") != 200:
            return shown
        try:
            config = answers.config(_data(read), str(entry.get("node")), int(entry["vmid"]), run.fleet["nic"])
            listed = answers.resource(entry)
        except (answers.ProxmoxError, KeyError, TypeError, ValueError):
            return shown
        try:
            found: Any = block.parse(config.description)
        except block.Unreadable as unreadable:
            found = unreadable
        compared = template.judge(self.base, listed, config, found, self.want, readable=True)
        shown["drift"] = list(compared.differences)
        if compared.refused:
            self.said.append(_item("todo", "template", _plain(compared.said(), 400)))
        return shown

    def _jobs(self, vms: Sequence[Mapping[str, Any]]) -> list[dict]:
        """A line for each VM whose record names a job this computer has no record of, which a run on another computer
        left, for each whose job here ended without saying how (lost), and for each whose record names a job that is
        no job's ID (runs/job.py's pattern), which no logs line can name: anyone who may set the VM's notes writes
        it."""
        items = []
        for vm in vms:
            job = str(self._fields(vm).get("job") or "")
            name = str(vm.get("name") or "")
            if not job or not name:
                continue
            if not full("job_id", job):
                text = f"its record names a job that is no job's ID ({_plain(job)}), so no run's logs can be named"
                items.append(_item("problem", _plain(name), text))
                continue
            logs = Line("logs", {"job": job})
            try:
                here = jobs.find(self.jobs, job)
                state = jobs.state(here) if here is not None else None
            except (OSError, ValueError):
                continue
            if here is None:
                items.append(
                    _item(
                        "todo",
                        name,
                        f"its record names job {job}, which a run on another computer left: that run may be changing"
                        " it now, or ended before it took its line away",
                        printer.said("{logs} on that computer says how it went", {"logs": logs}),
                    )
                )
            elif state == "lost":
                items.append(
                    _item(
                        "problem",
                        name,
                        f"job {job}, which was changing it, ended without saying how it went (lost)",
                        printer.said("{logs} shows what it did", {"logs": logs}),
                    )
                )
        return items

    def report(self, run: Any, report: dict) -> dict:
        """The finished report, with this reader's lines beside the model's, ranked as the model ranks them, and the
        document's run time where a control VM gives an age."""
        report = dict(report)
        tag = str(run.settings.get("tag") or "agent-warehouse")
        kept = []
        for item in report.get("attention") or []:
            text = str(item.get("text") or "")
            # The model's lines of a name no VM of the fleet has, or two have, say it of the pool, not of a tag.
            if text.startswith((f"no VM tagged {tag} ", f"more than one VM tagged {tag} ")):
                item = {**item, "text": text.replace(f"VM tagged {tag}", f"VM of pool {self.pool}", 1)}
            kept.append(item)
        more = list(self.said) + self._jobs(report.get("vms") or [])
        if self.scope == "pool" and report.get("proxmox", {}).get("version"):
            more.append(
                _item(
                    "todo",
                    "proxmox",
                    f"the node's VMs outside pool {self.pool} were not read: the token reads its pool alone"
                    " (proxmox_audit_scope: pool), so the other VMs and the capacity they hold are not counted",
                )
            )
        report["attention"] = ranked(kept + [one for one in more if one not in kept], report.get("vms") or [])
        report["counts"] = {level: sum(1 for one in report["attention"] if one["level"] == level) for level in LEVELS}
        worst = next((level for level in LEVELS if report["counts"][level]), "")
        report["exit"] = EXIT_OF.get(worst, 0)
        report[KNOWN_AT] = report.get("now")
        return report


def ranked(items: Sequence[dict], vms: Sequence[Mapping[str, Any]]) -> list[dict]:
    """items once each and in the model's order: by level, worst first, then by the VM's place in the report."""
    seen: set[tuple[str, str, str]] = set()
    once = []
    for one in items:
        key = (one["level"], one["vm"], one["text"])
        if key not in seen:
            seen.add(key)
            once.append(one)
    order = {level: at for at, level in enumerate(LEVELS)}
    place = {vm.get("name"): at for at, vm in enumerate(vms)}
    return sorted(once, key=lambda one: (order[one["level"]], place.get(one["vm"], -1)))
