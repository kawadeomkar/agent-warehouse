"""The base template compared with what its settings would build, by GETs alone (docs/architecture/engine.md §16.4).

Every VM of the fleet is a full clone of the base template, so the template is what each VM starts as. `deploy` asks
`compare` whether it is what its settings would build; `status`, which may not import the engine (card G15), reads the
same through its own client and judges it by the same functions, pve/template.py's (card G16), so both say the same
outcome with the same lines: the judgement holds the record to the guest's own UUID whichever reader read it, and each
field's form is the reader's, here engine/record.py's `read`. The judgement is there, in plain code with no client: what
each outcome means, what a build makes (`wanted`, `BUILD`, `OPTIONS`) and what is said (`Compared`), which this module
gives as the comparison's.

`compare(client, settings, pool=, host_flag=)` reads the template through the API with the engine's one token, by GETs
alone: the cluster's listing; where nothing is listed at the template's ID, what the token may read there; and the
guest at that ID, its configuration with the record in its notes, only where the listing names one the judgement reads
further (pve/template.py's `listed`): a qemu guest, a member of the fleet's pool, on `proxmox_node`. The record is read
by engine/record.py's `read`, so a block it cannot read, a value that is not what its field may be, a copy of another
VM's record and a template's record that names no UUID (only a VM's first form names none) are each its Unreadable,
which the judgement refuses: never rebuilt or cloned. Where `proxmox_template_custom` names a template of the person's
own, the listing, the configuration of the one template of that name on the node, and where none of that name is
listed, what the token may read of every guest.

The token is the client's alone, and nothing here sends any request but a GET (tests/unit/test_aw_engine_template.py).
Proxmox's API paths are the client's: the one path written here is an ACL path, the template's ID under `/vms`, which
GET /access/permissions takes as its query.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..pve import template as judging
from ..pve.answers import Resource, VmConfig
from ..pve.client import Proxmox
from ..pve.template import (
    BUILD,
    CUSTOM,
    DIFFERENT,
    MISSING,
    OPTIONS,
    OUTCOMES,
    REFUSED,
    SAME,
    UNSEEN,
    Compared,
    wanted,
)
from . import record

__all__ = [
    "BUILD",
    "CUSTOM",
    "DIFFERENT",
    "MISSING",
    "OPTIONS",
    "OUTCOMES",
    "REFUSED",
    "SAME",
    "UNSEEN",
    "Compared",
    "compare",
    "wanted",
]


def _reads(client: Proxmox, path: str) -> bool:
    """Whether the token may read the guest at an ACL path, or every guest at GUESTS: what Proxmox says it holds there,
    so that a guest it could not see is never said to be missing."""
    return judging.AUDIT in client.permissions(path).privileges


def _found(config: VmConfig) -> judging.Fields | record.Unreadable | None:
    """The record config's notes hold, read by engine/record.py's typed rules, as the judgement takes it: its fields,
    None where the notes hold none, or the Unreadable the read raised, a Copy among them."""
    try:
        found = record.read(config)
    except record.Unreadable as unreadable:
        return unreadable
    return None if found is None else found.fields()


def _custom(
    client: Proxmox,
    settings: Mapping[str, object],
    listing: tuple[Resource, ...],
    name: str,
    node: str,
    pool: str,
    host_flag: str,
) -> Compared:
    """A template of the person's own, which proxmox_template_custom names: whether the token may read every guest,
    asked only where none of that name is listed, and the configuration of the one template of that name on the
    node, judged by pve/template.py's custom, which says init's line, for host_flag's host, where the token may not
    read it."""
    readable = bool(judging.named(listing, name)) or _reads(client, judging.GUESTS)
    template_id = judging.custom_id(listing, name, node)
    return judging.custom(
        listing,
        None if template_id is None else client.config(node, template_id),
        name=name,
        node=node,
        pool=pool,
        disk=str(judging.option(settings, "proxmox_vm_disk")),
        nic=str(settings["proxmox_nic"]),
        readable=readable,
        host_flag=host_flag,
    )


def compare(client: Proxmox, settings: Mapping[str, object], *, pool: str, host_flag: str) -> Compared:
    """Whether the base template on the host client reaches is what settings would build, by GETs alone: see
    pve/template.py's docstring for each outcome. settings are the run's against that host, every row's value; pool is
    the fleet's pool there, which every guest of the fleet is a member of; host_flag is the host a line names, as the
    printer of a command takes it ("" where the fleet has one host). A refusal of Proxmox's, the client's errors, is
    raised as it is."""
    if not isinstance(pool, str) or not pool:
        raise ValueError("compare is given the fleet's pool on the host")
    if not isinstance(host_flag, str):
        raise TypeError("compare is given the host's flag a line names, '' for none")
    node = str(settings["proxmox_node"])
    listing = client.resources("vm")
    custom = str(settings["proxmox_template_custom"] or "")
    if custom:
        return _custom(client, settings, listing, custom, node, pool, host_flag)
    base = judging.Base.of(settings, pool=pool, host_flag=host_flag)
    entry = judging.at(listing, base.template_id)
    readable = entry is None and _reads(client, f"{judging.GUESTS}/{base.template_id}")
    decided = judging.listed(base, entry, readable=readable)
    if decided is not None:
        return decided
    config = client.config(base.node, base.template_id)
    return judging.judge(base, entry, config, _found(config), wanted(settings), readable=readable)
