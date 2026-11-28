"""The probe's document as the states layer 2 and status decide from: found.py's records of a machine's reads
(docs/architecture/engine.md §16.10).

`read(job, target, parts, settings=..., ...)` runs the helper's `probe` on the machine (guest/remote.py) for the parts
asked - `system`, `claude`, `github`, `work`, `tailscale`, by the names each has in the probe's answer - and returns
what today's `lib/claude_vm/status/found.py` makes of it (`ssh_found`), the parts not asked left out: so that a
layer-2 step and status say each state by one definition, today's, which `tests/unit/test_status_vms.py` holds equal
to the roles' own tasks. `settings` is one machine's, in the shape status keeps (status/settings.py's `VM`, the
probe's ask under `asked`).

The probe runs as root only where a part root reads is asked (Tailscale's): then through `sudo`, by remote.py's rule,
and where `sudo` refuses, as the machine's user once more, the root part said not read, as today's `just status` does,
within what is left of `limits.seconds`, the whole read's time. Every other read is the user's, with no `sudo` at all.
`password` is the run's (remote.Password), handed to each read of the run. `document` gives the probe's document in
today's shape (status/reach.py's `read`): its reads, `{"unreachable": True, "msg": ...}` or
`{"failed": True, "msg": ...}`, and a verb out of its time keeps the reads it sent before apt's simulation, the
upgrades said not read.

found.py is reused as it stands, a name taken from it and never the module: today's `status` makes a TLS context
elsewhere, so this module is one tests/unit/test_aw_vendor_trust.py's CONTEXTS names, with why. Standard library and
found.py only; nothing of Ansible is loaded, now or when a machine is read.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from claude_vm.fleet import paths
from claude_vm.guest import bundle, remote
from claude_vm.ssh import runner
from claude_vm.status.found import ssh_found

if TYPE_CHECKING:
    from claude_vm.runs.job import Job

# The probe's parts, by the names each has in its answer, and those it reads as root.
PARTS = ("system", "claude", "github", "work", "tailscale")
AS_ROOT = frozenset({"tailscale"})
# What the probe is asked beside the parts, whatever they are: the machine's user and each read's limit.
ALWAYS = ("user", "limit")
# The view of status's (found.parts_wanted) that asks for each part: ssh_found reads the parts its sections want.
SECTIONS = {"system": "system", "work": "work", "claude": "sign-ins", "github": "sign-ins", "tailscale": "access"}
# The marker the github_projects role writes on a machine where gh's login is its own, holding the token's digest: the
# probe's github part reads it (status's ask, github's marker) to tell that login from one made by hand.
GITHUB_MARKER = paths.ON_A_MACHINE + "/gh-login"


def _parts(parts: Iterable[str]) -> frozenset[str]:
    if isinstance(parts, (str, bytes)) or not isinstance(parts, Iterable):
        raise runner.Refused(f"the parts read are a list of the probe's: {', '.join(PARTS)}")
    chosen = frozenset(parts)
    unknown = sorted(part for part in chosen if part not in PARTS)
    if unknown or not chosen:
        raise runner.Refused(f"the parts read are one or more of the probe's: {', '.join(PARTS)}")
    return chosen


def asked(ask: Mapping[str, object], parts: Iterable[str]) -> dict[str, object]:
    """What the probe is asked of ask, status's whole ask for a machine: the parts given, the user and the limit."""
    chosen = _parts(parts)
    return {key: value for key, value in ask.items() if key in chosen or key in ALWAYS}


def _document(outcome: remote.Outcome) -> dict:
    """The probe's answer in today's shape (status/reach.py's read): its last whole document; or, where it ended
    without one, the reads it sent before apt's simulation with the upgrades said not read; or why not."""
    if isinstance(outcome, remote.NotAnswered) and not outcome.reached:
        return {"unreachable": True, "msg": outcome.why}
    if isinstance(outcome, remote.NotRun):
        return {"failed": True, "msg": outcome.said}
    final, early = None, None
    for said in outcome.lines:
        if not isinstance(said, dict) or said.get("started") is True:
            continue
        if isinstance(said.get("early"), dict):
            early = said["early"]
        else:
            final = said
    if final is not None:
        return final
    why = outcome.why if isinstance(outcome, remote.NotAnswered) else outcome.said or f"exit {outcome.status}"
    if early is not None:
        system = early["user"].get("system") if isinstance(early.get("user"), dict) else None
        if isinstance(system, dict):
            system.pop("upgrades_pending", None)
            unread = system.get("unread")
            system["unread"] = {**(unread if isinstance(unread, dict) else {}), "upgrades": why}
        return early
    return {"failed": True, "msg": why}


async def document(
    job: Job,
    target: runner.Target,
    ask: Mapping[str, object],
    *,
    env: Mapping[str, str],
    password: remote.Password | None = None,
    limits: runner.Limits = runner.LIMITS,
    python: str = bundle.PYTHON,
    become_exe: str = remote.SUDO,
) -> dict:
    """What the probe read on target for ask, the probe's ask (asked's): as root where a part of AS_ROOT is asked,
    and where sudo refuses that, as the user once more within what is left of limits.seconds, root's part said not
    read; in today's shape."""
    if not isinstance(limits, runner.Limits):
        raise runner.Refused("limits is a Limits")
    until = time.monotonic() + limits.seconds
    common = {"env": env, "python": python, "become_exe": become_exe}
    try:
        outcome = await remote.helper(
            job,
            target,
            "probe",
            asked=dict(ask),
            as_root=bool(AS_ROOT & set(ask)),
            password=password,
            limits=limits,
            **common,
        )
        if not (isinstance(outcome, remote.NotRun) and outcome.sudo):
            return _document(outcome)
        alone = {key: value for key, value in ask.items() if key not in AS_ROOT}
        left = until - time.monotonic()
        if left <= 0:
            found = _document(remote.NotAnswered(f"it took longer than {limits.seconds:g} s", reached=True))
        else:
            given = dataclasses.replace(limits, seconds=left)
            found = _document(
                await remote.helper(job, target, "probe", asked=alone, as_root=False, limits=given, **common)
            )
    except OSError as error:
        return {"failed": True, "msg": str(error) or type(error).__name__}
    if "user" in found:
        return {**found, "root": {"failed": True, "msg": f"sudo refused: {outcome.said}"}}
    return found


def _narrowed(records: dict, chosen: frozenset[str]) -> dict:
    """ssh_found's records with the parts not asked left out, and the VPN with Tailscale's."""
    kept = {**records, **{part: None for part in PARTS if part not in chosen}}
    if "tailscale" not in chosen:
        kept["vpn"] = None
    kept["unread"] = {part: why for part, why in records["unread"].items() if part in chosen}
    return kept


async def read(
    job: Job,
    target: runner.Target,
    parts: Iterable[str],
    *,
    settings: Mapping[str, object],
    name: str,
    host: str = "",
    joined_before: bool = False,
    env: Mapping[str, str],
    password: remote.Password | None = None,
    limits: runner.Limits = runner.LIMITS,
    python: str = bundle.PYTHON,
    become_exe: str = remote.SUDO,
) -> dict:
    """found.py's records of parts on target, the machine name: ssh_found's, with settings, the machine's in status's
    shape, host the address its lines name and joined_before whether its Tailscale had a login (from Proxmox), each
    part not asked None and left out of what was not read."""
    chosen = _parts(parts)
    ask = settings.get("asked")
    if not isinstance(ask, Mapping):
        raise runner.Refused("a machine's settings hold the probe's ask, under asked")
    found = await document(
        job,
        target,
        asked(ask, chosen),
        env=env,
        password=password,
        limits=limits,
        python=python,
        become_exe=become_exe,
    )
    sections = sorted({SECTIONS[part] for part in chosen})
    return _narrowed(ssh_found(found, settings, name, host, sections, joined_before), chosen)
