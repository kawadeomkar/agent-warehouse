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

`build(ctx, host, settings)` builds the base template on host over root SSH with today's role's steps (card E12),
holding the host's template section exclusively (runs/locks.py), so that it waits for clones in flight and they for it.
The image's checksum is held to its form first: an image with no checksum to hold it to, a custom image with the
default's, and a signature with no list to sign are refused before anything is read. Then it reads, as root, what the
whole cluster holds at the template's ID and the configuration of a guest there, and judges them by the functions
`compare` judges by, root seeing what the token may not: a guest there that is no template of this engine's, or outside
the pool, or on another node, is refused as `compare` refuses it and never touched; the same template is left as it is,
and so is one whose differences `proxmox_template_rebuild: false` keeps; a template of the person's own
(`proxmox_template_custom`) is never built. Then, on this computer, the image's SHA-256 from its checksum list, verified
by vendors/downloads.py (Ubuntu's signed list, or an unsigned list, or the digest itself). On the host, each step one
command of pve/commands.py's through the SSH runner, nothing on its standard input: the image fetched by the host's curl
into the engine's own image directory (`IMAGE_DIR`, or `proxmox_template_image_dir`), made 0755 and refused unless it is
a directory itself, never a link, of the account that builds and writable by no other, under a name of its own that
anything there is cleared from first, held by the host's sha256sum to the digest verified here, removed and the build
stopped, naming both, where it differs, and renamed into place over whatever has the image's name, never into where a
link there leads; where the guest agent goes in, the image tool installed by apt where
`proxmox_template_manage_libguestfs` is on and dpkg's own state of it is not installed (one held is there, and one
neither installed nor absent, or whose state could not be read, stops the build), the image's system read and one
outside the Debian family refused by name, a copy customised, its name cleared first; the template that differs removed
only once judged again just before, still this engine's template that differs, with its record, its name and the pool,
under the UUID first judged (`Compared.identity`), and anything that changed in between left as it is, a refusal of
Proxmox's - a linked clone's - said; `qm create` with the record as its description, its SMBIOS UUID made here so that
the record names it from its first write, and the pool; the disk imported; the cloud-init drive, the boot order and
DHCP, with no `ciupgrade`; `qm template`; and the template read back and judged again, which must find it as its
settings would build it. The customised copy is removed whatever happens. What the build leaves on the host is recorded
in host.yml's `made` through fleet/hosts.py's one writer as it is made: each image file it downloaded, the template by
its ID, and each package of the image tool this run installed (none that was there before), so that a cleanup removes
exactly that.

`ensure(ctx, host, settings, terminal=)` is the first link of `deploy`, and the whole of `deploy --only template` (card
E13), as the run's `template` stage: `compare` first. A template a VM may be cloned from as it is (`usable`: the same,
a difference `proxmox_template_rebuild: false` keeps, a person's own lacking nothing) is said and left, never built;
this engine's, where its configuration has no cloud-init drive, which the judgement does not read (`cloud_init`), is
refused rather than called the same. A refused template, a cut build's VM at the ID among them, and a person's own that
cannot be cloned or read, are refused with the judgement's sentence, never built over or retried. Otherwise (missing,
unseen, or a difference to rebuild) a standing way to build is asked for: root SSH that answers in batch mode, one
connection that runs nothing but `true` (every ssh the runner starts carries BatchMode=yes). Where it answers, `build`
runs, at a terminal or with nobody there; where it does not, nothing is created: at a terminal, the sentence says what
would give one; with no terminal, `Compared.unbuilt`'s, with what the template has and what is wanted, why this run
cannot build it, and the `deploy --only template` line to run at a terminal. Every refusal of ensure's own is also the
run's NEXT STEPS line (matter `template`), and no run clones from a template that is to be built and was not: ensure
returns only a usable template, or raises. Refused before anything is built; Failed where the build began and did not
end well.
"""

from __future__ import annotations

import dataclasses
import json
import posixpath
import re
import urllib.parse
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .. import proxmox
from ..fleet import hosts, paths
from ..pve import answers, commands
from ..pve import record as block
from ..pve import template as judging
from ..pve.answers import Resource, VmConfig
from ..pve.client import Proxmox, ProxmoxError
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
    cloud_init,
    option,
    wanted,
)
from ..runs import lock_sets, locks
from ..runs.stages import DEPLOY, Declared
from ..schema import printer
from ..ssh import runner
from ..text import one_line
from ..vendors import downloads, proxy, trust
from ..vendors.transport import TransportError
from . import record, routes

if TYPE_CHECKING:
    from ..runs.attached import Context
    from .fleet import Host

__all__ = [
    "BUILD",
    "CUSTOM",
    "DECLARED",
    "DIFFERENT",
    "IMAGE_DIR",
    "MISSING",
    "OPTIONS",
    "OUTCOMES",
    "REFUSED",
    "SAME",
    "UNSEEN",
    "Compared",
    "Failed",
    "Refused",
    "build",
    "cloud_init",
    "compare",
    "ensure",
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


# ------------------------------------------------------------------------------------------- the build ----

# The engine's own image directory on the host, where proxmox_template_image_dir names none: never today's role's.
IMAGE_DIR = "/var/lib/vz/aw-images"
# A mode as stat's %a says it: octal, with the set-ID and sticky bits where set.
OCTAL = re.compile(r"[0-7]{3,4}")
# The image tool, as today's role installs it: dhcpcd-base beside libguestfs-tools, which Proxmox VE 9 does not pull
# in, and without which the tool's appliance has no DHCP and --install cannot resolve a mirror.
IMAGE_TOOL = ("libguestfs-tools", "dhcpcd-base")
# dpkg's state of a package that is there, and those of one that is not, as `dpkg-query --show` says them, and its
# words for a package it has never had: any other state (half installed, unpacked, half configured, awaiting triggers)
# is neither, and the image tool is never installed over it.
PRESENT = "installed"
ABSENT = ("not-installed", "config-files")
NONE_FOUND = "no packages found matching"
# A checksum list of Ubuntu's own, which Ubuntu signs beside it, as today's role tells one: its signature is the list's
# URL and .gpg, unless proxmox_template_image_signature names another, or "" for none.
UBUNTU_LIST = re.compile(r"sha256:https?://cloud-images[.]ubuntu[.]com/\S*/SHA256SUMS")
# A digest given as the checksum itself, and a checksum list's URL.
DIGEST = re.compile(r"[0-9a-fA-F]{64}")
LISTED = re.compile(r"https?://\S{1,4000}")
# What a command over root SSH may take, none with a time to say a first word, since qm says nothing as it works: a
# read; a short step; and a long one, a download, a digest of an image, the image tool and apt, the disk's import.
READ = runner.Limits(seconds=runner.SECONDS, silent=None)
SHORT = runner.Limits(seconds=300, silent=None)
LONG = runner.Limits(seconds=3600, silent=None)
# The most characters of what the host said that a sentence quotes.
SAID_MOST = 300


class Refused(Exception):
    """The base template not built, said in a sentence a person reads: what stopped the build, and that nothing past
    it was done."""


class Failed(Refused):
    """The build ensure began did not end well (build's Refused): what it made is in host.yml's made."""


@dataclasses.dataclass(frozen=True)
class _Image:
    """The image a build makes the template from, as the settings give it: its URL, what its checksum gives after
    `sha256:` (a list's URL, or the digest itself), the URL of the list's signature ("" for none), its file's name,
    where it is kept on the host (path, beside part, its download's name until it is held to its digest), the copy the
    image tool changes ("" where the guest agent does not go in and the download itself is imported), the packages put
    into it, and whether the build installs the image tool."""

    url: str
    checksum: str
    signature: str
    file_name: str
    directory: str
    path: str
    part: str
    copy: str
    packages: tuple[str, ...]
    manage: bool

    @property
    def imported(self) -> str:
        return self.copy or self.path

    @classmethod
    def of(cls, settings: Mapping[str, object], template_id: int) -> _Image:
        url = str(option(settings, "proxmox_template_image_url"))
        checksum = str(option(settings, "proxmox_template_image_checksum") or "")
        default_url = str(judging.OPTIONS["proxmox_template_image_url"])
        if url != default_url and checksum in ("", judging.OPTIONS["proxmox_template_image_checksum"]):
            raise Refused(
                f"proxmox_template_image_url is set to {one_line(url, SAID_MOST)}, with no "
                "proxmox_template_image_checksum of its own. Give it the image's checksum (sha256:<digest>, or "
                "sha256:<URL of a checksum list>), so a corrupt or swapped download is refused rather than built into "
                "every VM. Nothing was built."
            )
        if not checksum:
            raise Refused(
                f"{one_line(url, SAID_MOST)} has no checksum to hold it to: set proxmox_template_image_checksum to "
                "sha256:<the URL of its checksum list>, or sha256:<its digest>. Nothing was built."
            )
        algorithm, _, given = checksum.partition(":")
        if algorithm != "sha256" or not (DIGEST.fullmatch(given) or LISTED.fullmatch(given)):
            raise Refused(
                "proxmox_template_image_checksum is sha256:<the URL of the image's checksum list>, or "
                "sha256:<its digest>: the host holds the image to its SHA-256. Nothing was built."
            )
        signature = settings.get("proxmox_template_image_signature")
        if signature is None:
            signature = f"{given}.gpg" if UBUNTU_LIST.fullmatch(checksum) else ""
        signature = str(signature)
        if signature and not LISTED.fullmatch(given):
            raise Refused(
                "proxmox_template_image_signature is set, so proxmox_template_image_checksum must be sha256:<the URL "
                'of the checksum list it signs>: set proxmox_template_image_signature to "" for an image you hold to '
                "its checksum alone. Nothing was built."
            )
        file_name = posixpath.basename(urllib.parse.urlsplit(url).path)
        if not commands.PART.fullmatch(file_name) or not commands.URL.fullmatch(url):
            raise Refused(
                f"proxmox_template_image_url, {one_line(url, SAID_MOST)}, is no http or https URL of an image file of "
                "plain characters. Nothing was built."
            )
        directory = str(settings.get("proxmox_template_image_dir") or IMAGE_DIR)
        agent = option(settings, "proxmox_template_install_guest_agent") is True
        packages = option(settings, "proxmox_template_customize_packages")
        if not isinstance(packages, (list, tuple)) or not all(isinstance(each, str) for each in packages):
            raise TypeError("proxmox_template_customize_packages is a list of packages' names")
        return cls(
            url=url,
            checksum=given,
            signature=signature,
            file_name=file_name,
            directory=directory,
            path=f"{directory}/{file_name}",
            part=f"{directory}/{file_name}.part",
            copy=f"{directory}/agent-warehouse-{template_id}.img" if agent else "",
            packages=tuple(packages) if agent else (),
            manage=option(settings, "proxmox_template_manage_libguestfs") is True,
        )


class _Host:
    """The Proxmox host as root, through the SSH runner, as a child of the run's job: each command an argument list,
    nothing on its standard input."""

    def __init__(self, ctx: Context, target: runner.Target, env: Mapping[str, str]) -> None:
        self.ctx, self.target, self.env = ctx, target, env

    async def __call__(self, command: Sequence[str], limits: runner.Limits = SHORT) -> runner.Result:
        try:
            return await runner.run(self.ctx.job, self.target, list(command), None, limits, self.env)
        except runner.Refused as error:
            raise Refused(f"Root SSH to the Proxmox host was refused: {one_line(str(error), SAID_MOST)}.") from None


def _ok(result: runner.Result) -> bool:
    return isinstance(result, runner.Done) and result.status == 0


def _why(result: runner.Result) -> str:
    """Why a command over root SSH did not end well, in plain words: what the host said."""
    if isinstance(result, runner.Done):
        said = one_line(result.said, SAID_MOST) or "nothing"
        return f"it ended with status {result.status}, saying {said}"
    if isinstance(result, runner.Cut):
        return one_line(f"it sent more than was asked: {result.why}", SAID_MOST)
    if isinstance(result, runner.Silent):
        return "it said nothing in time"
    if isinstance(result, runner.TimedOut):
        return "it did not end in time"
    return one_line(result.said, SAID_MOST) or "the host could not be reached, or the command ended with status 255"


async def _must(at: _Host, command: Sequence[str], limits: runner.Limits, what: str) -> runner.Done:
    """command run, which must end well: else Refused, saying what was not done and why."""
    result = await at(command, limits)
    if not _ok(result):
        raise Refused(f"{what} on the Proxmox host: {_why(result)}. The base template was not built.")
    assert isinstance(result, runner.Done)
    return result


async def _json(at: _Host, command: Sequence[str], what: str) -> object:
    result = await _must(at, command, READ, f"{what} could not be read")
    try:
        return json.loads("\n".join(result.lines))
    except ValueError:
        raise Refused(f"{what} read on the Proxmox host is no JSON. The base template was not built.") from None


async def _config(at: _Host, base: judging.Base, nic: str) -> VmConfig:
    """The configuration of the guest at base's ID, as root reads it on its node."""
    value = await _json(at, commands.config(base.node, base.template_id), f"The configuration at ID {base.template_id}")
    try:
        return answers.config(value, base.node, base.template_id, nic)
    except answers.Malformed as error:
        raise Refused(f"{error}. The base template was not built.") from None


async def _judged(at: _Host, base: judging.Base, settings: Mapping[str, object]) -> Compared:
    """What the guest at base's ID is, judged as compare judges it, from what root reads: the whole cluster's listing,
    and where it names a guest the judgement reads further, that guest's configuration."""
    try:
        listing = answers.resources(await _json(at, commands.LISTING, "The cluster's listing of guests"))
    except answers.Malformed as error:
        raise Refused(f"{error}. The base template was not built.") from None
    entry = judging.at(listing, base.template_id)
    decided = judging.listed(base, entry, readable=True)
    if decided is not None:
        return decided
    config = await _config(at, base, str(settings["proxmox_nic"]))
    return judging.judge(base, entry, config, _found(config), wanted(settings), readable=True)


async def _digest(settings: Mapping[str, object], image: _Image, environ: Mapping[str, str]) -> str:
    """The image's SHA-256, worked out on this computer before anything is fetched on the host: from Ubuntu's signed
    checksum list (or the list and signature the settings name), verified by vendors/downloads.py; from an unsigned list
    where no signature is set; or the digest the checksum gives."""
    import asyncio

    given, signature = image.checksum, image.signature
    if DIGEST.fullmatch(given):
        return given.lower()
    setting = str(settings["network_proxy"])
    tls_ca_file = settings["tls_ca_file"] or None
    key = settings.get("proxmox_template_image_key")
    fingerprint = settings.get("proxmox_template_image_fingerprint") or None
    keyed = {"key": str(key), "fingerprint": None if fingerprint is None else str(fingerprint)} if key else {}
    try:
        if signature:
            return await asyncio.to_thread(
                downloads.signed_digest,
                given,
                signature,
                image.file_name,
                **keyed,
                tls_ca_file=tls_ca_file,
                list_proxy=proxy.choose(setting, environ, given),
                signature_proxy=proxy.choose(setting, environ, signature),
            )
        return await asyncio.to_thread(
            downloads.listed_digest,
            given,
            image.file_name,
            tls_ca_file=tls_ca_file,
            proxy=proxy.choose(setting, environ, given),
        )
    except (downloads.DownloadError, trust.Unusable, ValueError, TypeError) as error:
        raise Refused(
            f"The image's SHA-256 was not taken, so nothing was fetched on the host: {one_line(str(error), None)}."
        ) from None


class _Made:
    """What the build made on the host, recorded in host.yml's made as it is made, through fleet/hosts.py's one writer:
    each image file downloaded, the template by its ID, each package installed."""

    TRIES = 3

    def __init__(self, layout: hosts.Layout, name: str) -> None:
        self.layout, self.name = layout, name

    def add(self, what: str, made_id: str) -> None:
        entry = {"what": what, "id": made_id, "by": hosts.MADE_BY[0]}

        def change(raw: dict) -> None:
            recorded = list(raw.get("made") or [])
            seen = {(each.get("what"), str(each.get("id"))) for each in recorded if isinstance(each, dict)}
            if (what, made_id) not in seen:
                raw["made"] = [*recorded, entry]

        for tried in range(1, self.TRIES + 1):
            try:
                hosts.write(self.layout, self.name, self.layout.state(self.name).digest, change=change)
                return
            except hosts.Refused as error:
                if tried == self.TRIES:
                    raise Refused(
                        f"The {what} {one_line(made_id, SAID_MOST)} was made on the Proxmox host, but host.yml's made "
                        f"could not record it ({one_line(str(error), SAID_MOST)}): add it there by hand, or a cleanup "
                        "leaves it. The base template was not built."
                    ) from None


async def _fetched(at: _Host, image: _Image, digest: str, made: _Made) -> None:
    """The image in the image directory, held to digest by the host's own sha256sum: left where it is already, else
    fetched by the host's curl under a name of its own, held, and renamed into place; one that differs is removed."""
    await _folder(at, image)
    if _first_word(await at(commands.digest_of(image.path), LONG)) == digest:
        return
    # Whatever has the download's name goes first, a link above all, which curl would write through.
    await _must(at, commands.removed(image.part), SHORT, "The image's download could not be cleared")
    fetched = await at(commands.fetch(image.url, image.part), LONG)
    if not _ok(fetched):
        await at(commands.removed(image.part))
        raise Refused(
            f"The image {one_line(image.url, SAID_MOST)} could not be fetched on the Proxmox host: {_why(fetched)}. "
            "The base template was not built."
        )
    found = _first_word(await at(commands.digest_of(image.part), LONG))
    if found != digest:
        await at(commands.removed(image.part))
        raise Refused(
            f"The image fetched from {one_line(image.url, SAID_MOST)} has the SHA-256 {found or 'of nothing read'}, "
            f"not {digest}, the one its checksum gives: it was removed, and the base template was not built."
        )
    await _must(at, commands.put_in_place(image.part, image.path), SHORT, "The image could not be put in place")
    made.add("file", image.path)


async def _folder(at: _Host, image: _Image) -> None:
    """The image directory made where it is not there, and held to what no account but the one that builds may write
    in, so that no name in it was planted by another to be written through: the building account's own (root's, over
    root SSH), writable by no one else, and a directory itself, never a link to one."""
    where = image.directory
    await _must(at, commands.folder(where), SHORT, "The image directory could not be made")
    builder = _first_word(await _must(at, commands.account(), SHORT, "The account that builds could not be read"))
    found = await _must(at, commands.kind_of(where), SHORT, f"The image directory {where} could not be read")
    words = "\n".join(found.lines).strip().split(" ", 2)
    if len(words) != 3 or not builder.isdigit() or not words[0].isdigit() or not OCTAL.fullmatch(words[1]):
        raise Refused(
            f'The image directory {where} could not be read: stat said "{one_line(" ".join(words), SAID_MOST)}". The '
            "base template was not built."
        )
    owner, mode, kind = words
    if kind != "directory":
        raise Refused(
            f"The image directory {where} is a {one_line(kind, SAID_MOST)}, not a directory, and the build would write "
            "where it leads: make it a directory of root's, or name another in proxmox_template_image_dir. The base "
            "template was not built."
        )
    if owner != builder:
        raise Refused(
            f"The image directory {where} belongs to the account numbered {owner}, not to root, which builds: that "
            "account could leave a link in it for root to write through. Give it to root, or name another in "
            "proxmox_template_image_dir. The base template was not built."
        )
    if int(mode, 8) & 0o022:
        raise Refused(
            f"The image directory {where} may be written by accounts other than root (mode {mode}), any of which could "
            "leave a link in it for root to write through: make it 0755, or name another in "
            "proxmox_template_image_dir. The base template was not built."
        )


def _first_word(result: runner.Result) -> str:
    """The first word a command printed, where it ended well: sha256sum's digest."""
    if not _ok(result) or not result.lines:  # type: ignore[union-attr]
        return ""
    words = result.lines[0].split()  # type: ignore[union-attr]
    return words[0] if words else ""


async def _customised(at: _Host, image: _Image, made: _Made) -> None:
    """The guest agent put into a copy of the image: the image tool installed where it is missing and the build manages
    it, the image's system read and one outside the Debian family refused, the copy made and changed."""
    if image.manage:
        missing = [package for package in IMAGE_TOOL if not await _installed(at, package)]
        if missing:
            await _must(at, commands.update(), LONG, "apt could not read its lists")
            await _must(at, commands.install(missing), LONG, f"apt could not install {', '.join(missing)}")
            for package in missing:
                made.add("package", package)
    system = await _must(at, commands.os_release(image.path), LONG, "The image tool could not read the image's system")
    text = "\n".join(system.lines)
    if not proxmox.os_release_debian(text):
        named = next((line for line in system.lines if line.startswith("ID=")), "") or "nothing"
        raise Refused(
            f'{one_line(image.url, SAID_MOST)} is no Ubuntu or Debian image: its os-release says "'
            f"{one_line(named, SAID_MOST)}\". A VM's configuration installs with apt, so only those can be a base. The "
            "base template was not built."
        )
    # Whatever has the copy's name goes first, a link above all, which cp would write through.
    await _must(at, commands.removed(image.copy), SHORT, "The image's copy could not be cleared")
    await _must(at, commands.copied(image.path, image.copy), LONG, "The image could not be copied")
    customised = commands.customised(image.copy, image.packages)
    await _must(at, customised, LONG, "The image tool could not put the guest agent into the image")


async def _installed(at: _Host, package: str) -> bool:
    """Whether dpkg says package is on the host: True for one installed, held or not, False for one it does not have;
    Refused for any other state and for an answer that did not come back, so that a package there before is never
    installed again and recorded in `made` as this run's."""
    result = await at(commands.installed(package))
    if isinstance(result, runner.Done):
        state = "\n".join(result.lines).strip()
        if result.status == 0 and state == PRESENT:
            return True
        if result.status == 0 and state in ABSENT:
            return False
        if result.status == 1 and not state and NONE_FOUND in result.said:
            return False
        if result.status == 0:
            raise Refused(
                f'dpkg says {package} is "{one_line(state, SAID_MOST)}" on the Proxmox host, neither installed nor '
                "absent: the image tool is not installed over it, and the base template was not built. Finish or "
                "remove that package on the host, then run again."
            )
    raise Refused(
        f"Whether {package} is installed could not be read on the Proxmox host: {_why(result)}. The image tool is not "
        "installed without knowing, and the base template was not built."
    )


async def _destroyed(at: _Host, base: judging.Base, found: Compared, settings: Mapping[str, object]) -> None:
    """The template found to differ removed, once judged again just before: only while the judgement still finds this
    engine's template that differs, its record, its name, its node and the pool as the first did, and under the SMBIOS
    UUID first judged (Compared.identity). One that changed in the minutes the image took - taken out of the pool,
    stripped of its record, renamed, or another guest - is left as it is, said with the judgement's sentence. Proxmox's
    refusal, a linked clone's, is said."""
    now = await _judged(at, base, settings)
    if now.outcome != DIFFERENT or not found.identity or now.identity != found.identity:
        raise Refused(
            f"The guest at ID {base.template_id} changed after it was judged to differ from its settings, so it is "
            f"left as it is and the base template was not built: {now.said()}"
        )
    removed = await at(commands.destroy(base.template_id))
    if not _ok(removed):
        raise Refused(
            f'The base template "{base.name}" ({base.template_id}) differs from its settings, but Proxmox would not '
            f"remove it to rebuild it: {_why(removed)}. Proxmox keeps a template a linked clone still uses: set "
            "proxmox_template_rebuild to false to keep this one as it is until none does."
        )


def _target(host: Host) -> runner.Target:
    """Root SSH's Target to host, as routes.py makes it, the one function that does."""
    fields = host.fields
    try:
        return routes.host_target(fields.address, fields.ssh_user, fields.ssh_port, host.values, host.environ)
    except (runner.Refused, paths.Refused) as error:
        raise Refused(f"Root SSH to the Proxmox host cannot be made: {one_line(str(error), SAID_MOST)}.") from None


async def build(
    ctx: Context, host: Host, settings: Mapping[str, object], *, target: runner.Target | None = None
) -> Compared:
    """The base template on host built with today's role's steps over root SSH, where it is missing or differs from
    settings (see the module's docstring): the run's settings against host, every row's value, as compare takes them;
    target the host's root SSH, routes.py's unless given. Returns the template as it is judged after: as its settings
    would build it, or as it was where nothing was to be built (the same, or a difference kept). Refused, with the
    sentence that says why, for every refusal and every step that did not end well."""
    base = judging.Base.of(settings, pool=host.pool, host_flag=host.host_flag)
    custom = str(settings["proxmox_template_custom"] or "")
    if custom:
        raise Refused(
            f'proxmox_template_custom names a template of your own, "{one_line(custom, SAID_MOST)}", which is only '
            "read and cloned: no base template is built."
        )
    image = _Image.of(settings, base.template_id)
    at = _Host(ctx, target if target is not None else _target(host), host.environ)
    async with locks.section(ctx, lock_sets.section_name("template", host.name)):
        found = await _judged(at, base, settings)
        if found.refused:
            raise Refused(found.said())
        if found.outcome == SAME or (found.outcome == DIFFERENT and not found.rebuild):
            return found
        if found.outcome == DIFFERENT:
            ctx.emit("report", None, f"{found.said()} It is rebuilt, at the same ID.")
        digest = await _digest(settings, image, host.environ)
        made = _Made(host.layout, host.name)
        made_uuid = str(uuid.uuid4())
        want = wanted(settings)
        try:
            made_record = record.Record(
                kind="template",
                uuid=made_uuid,
                build=BUILD,
                image=digest,
                image_url=image.url,
                packages=image.packages,
            )
            create = commands.create(
                base.template_id,
                name=base.name,
                ostype=want["ostype"],
                cpu=want["cpu"],
                cores=option(settings, "proxmox_template_cores"),  # type: ignore[arg-type]
                memory=option(settings, "proxmox_template_memory_mb"),  # type: ignore[arg-type]
                bridge=want["bridge"],
                uuid=made_uuid,
                notes=block.render("", made_record.fields()),
                pool=base.pool,
            )
            steps = (
                commands.import_disk(base.template_id, want["storage"], image.imported),
                commands.cloud_init(base.template_id, want["storage"]),
                commands.template(base.template_id),
            )
        except (TypeError, ValueError) as error:
            raise Refused(f"The base template's settings cannot make it: {error}. Nothing was built.") from None
        try:
            await _fetched(at, image, digest, made)
            if image.copy:
                await _customised(at, image, made)
            if found.outcome == DIFFERENT:
                await _destroyed(at, base, found, settings)
            await _must(at, create, SHORT, f'The template "{base.name}" ({base.template_id}) could not be created')
            made.add("template", str(base.template_id))
            for step in steps:
                await _must(at, step, LONG, f'The template "{base.name}" ({base.template_id}) could not be made')
        finally:
            if image.copy:
                await at(commands.removed(image.copy))
        built = await _judged(at, base, settings)
        if built.outcome != SAME or built.identity != made_uuid:
            raise Refused(
                f'The template "{base.name}" ({base.template_id}) was built but reads back as other than its settings '
                f"would build it - {built.said()} - so every run would rebuild it."
            )
        ctx.emit("report", None, f'The base template "{base.name}" ({base.template_id}) was built.')
        return built


# ------------------------------------------------------------------------------------------- ensure ----

# The template's stage of deploy, as runs/stages.py's DEPLOY declares it: the host's work, no machine's.
STAGE = "template"
DECLARED = Declared("deploy", tuple(stage for stage in DEPLOY.stages if stage.key == STAGE))
# Root SSH asked whether it answers in batch mode: one connection that runs nothing but `true`, which says nothing.
STANDING = runner.Limits(seconds=30.0, silent=None)


def _no_way(found: Compared, host: Host, why: str) -> str:
    """What a run at a terminal says where root SSH gives it no standing way to build (why): what would give one."""
    line = printer.said("{line}", {"line": found.line})
    account = one_line(host.fields.ssh_user, SAID_MOST)
    return (
        f"{found.said()} It is not built: root SSH to {host.name} as {account} does not answer with nobody asked "
        f"({why}), and the build runs over it. Give that account this computer's public key on the host "
        "(ssh-copy-id), load the key into ssh-agent where it has a passphrase, or name it in ssh_identity_file; then "
        f"run {line} again."
    )


def _drive(host: Host, found: Compared) -> bool:
    """Whether the template found has a cloud-init drive, its configuration read once through the API."""
    assert found.template_id is not None
    return cloud_init(host.client.config(found.node, found.template_id, host.nic))


async def _standing(ctx: Context, host: Host, target: runner.Target | None) -> tuple[runner.Target | None, str]:
    """Root SSH to host as a standing way to build: its Target, and "" where one connection that runs nothing answers
    in batch mode; else why not, in plain words."""
    try:
        at = target if target is not None else _target(host)
        answered = await _Host(ctx, at, host.environ)(["true"], STANDING)
    except Refused as error:
        return None, one_line(str(error), SAID_MOST).removesuffix(".")
    return at, "" if _ok(answered) else _why(answered)


async def _ensured(
    ctx: Context, host: Host, settings: Mapping[str, object], terminal: bool, target: runner.Target | None
) -> Compared:
    import asyncio

    try:
        found = await asyncio.to_thread(compare, host.client, settings, pool=host.pool, host_flag=host.host_flag)
        drive = found.custom or not found.usable or await asyncio.to_thread(_drive, host, found)
    except (ProxmoxError, TransportError) as error:
        raise Refused(f"The base template on {host.name} could not be read: {one_line(str(error), None)}") from None
    if not drive:
        raise Refused(found.without_drive(str(option(settings, "proxmox_template_storage"))))
    if found.usable:
        ctx.emit("report", None, found.said())
        return found
    if found.refused or found.custom:
        raise Refused(found.said())
    at, why = await _standing(ctx, host, target)
    if why and not terminal:
        raise Refused(found.unbuilt(host.name, why))
    if why or at is None:
        raise Refused(_no_way(found, host, why))
    try:
        return await build(ctx, host, settings, target=at)
    except Refused as error:
        raise Failed(str(error)) from None


async def ensure(
    ctx: Context, host: Host, settings: Mapping[str, object], *, terminal: bool, target: runner.Target | None = None
) -> Compared:
    """The base template on host made what a VM may be cloned from, as the run's template stage (see the module's
    docstring): the run's settings against host, every row's value, as compare takes them; terminal, whether a person
    is there to read what would give a standing way to build (runs/ask.py's terminal, decided before the run draws);
    target the host's root SSH, routes.py's unless given. Returns the template as it is now: usable. Refused, with the
    sentence, which is also the run's NEXT STEPS line, before anything is built; Failed where the build began."""
    ctx.emit("stage", None, STAGE)
    try:
        found = await _ensured(ctx, host, settings, terminal, target)
    except Refused as error:
        if not isinstance(error, Failed):
            ctx.emit("next", None, str(error), matter=STAGE)
        ctx.emit("stage", None, STAGE, outcome="failed", why=str(error))
        raise
    ctx.emit("stage", None, STAGE, outcome="done")
    return found
