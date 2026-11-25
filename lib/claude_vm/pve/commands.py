"""The commands a build of the base template runs on the Proxmox host over root SSH, each an argument list: plain code,
which runs nothing (docs/architecture/engine.md §16.5).

engine/template.py's `build` runs each through the SSH runner as root on the host, which joins its words with
shlex.join, so that the host's shell reads every value as one word: nothing of the project's is sent to the host, no
script and no program, and nothing on a command's standard input. They are the host's own programs, by name, as
root's PATH there finds them: Proxmox's `pvesh` and `qm`, libguestfs's `virt-cat` and `virt-customize`, `apt-get` and
`dpkg-query`, `curl`, `sha256sum` and coreutils, as a stock Proxmox VE host has them, and today's role runs them.

Every value is held to its form before a list is made, so that no word a setting gave is read as another option, a
second word or a path outside the one meant: a guest's ID a whole number in Proxmox's range; a path absolute, of plain
characters, with no `.` or `..` part; a file's name and a package's as Debian and the image directory take them; a node,
a storage, a bridge and a pool as Proxmox names them; a URL one word of http or https. A refusal is a ValueError that
names the setting's part, never its value.

- `LISTING` is the whole cluster's guests as root reads them, which the token may not (`proxmox_audit_scope: pool`):
  the JSON of /cluster/resources, as the API's own listing gives it (pve/answers.py's `resources`).
- `config(node, guest)` is a guest's configuration, as the API gives it, with its digest and its notes decoded
  (pve/answers.py's `config`): qm config prints neither.
- `create(...)` is qm create with the template's settings, its SMBIOS UUID, its record as its description and its pool:
  the template's first write, made before the template exists, so that its record names its UUID from the start
  (engine/record.py: only a VM's first form names none) and no client can send it. The notes are pve/record.py's
  `render`, bounded, and nothing else (`tests/unit/test_aw_record.py`'s scan names this module).
- `import_disk`, `cloud_init` and `template` make it what a clone starts as: the disk imported, the cloud-init drive,
  the boot order and DHCP, with no `ciupgrade`, so that a clone's first boot upgrades its packages by Proxmox's own
  default (card O15, item 5), and the template made; `destroy` removes one, which Proxmox refuses while a linked clone
  uses it.
- `fetch`, `digest_of`, `put_in_place`, `removed`, `folder` and `copied` are the image's: fetched by the host's curl to
  a name of its own, held by the host's sha256sum, renamed into place; `os_release` and `customised` are the image
  tool's; `installed` and `install` are apt's, `update` before them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..checks import proxmox_name
from .template import SCSIHW, SERIAL0, VGA

__all__ = [
    "LISTING",
    "OS_RELEASE",
    "cloud_init",
    "config",
    "copied",
    "create",
    "customised",
    "destroy",
    "digest_of",
    "fetch",
    "folder",
    "import_disk",
    "install",
    "installed",
    "os_release",
    "put_in_place",
    "removed",
    "template",
    "update",
]

# The whole cluster's guests, every kind and node, as JSON on one line.
LISTING = ("pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json")
# The file a cloud image's system is named in.
OS_RELEASE = "/etc/os-release"
# A guest's ID, as Proxmox takes one.
LOWEST, HIGHEST = 100, 999_999_999
# A path's part, a file's name: plain characters, never beginning with a dash, which a program reads as an option, or
# a dot, so that no part is . or ..
PART = re.compile(r"[A-Za-z0-9_+][A-Za-z0-9._+-]{0,254}")
# An image's URL: one word of http or https.
URL = re.compile(r"https?://[!-~]{1,4000}")
# A Debian package's name.
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]{1,99}")
# The SMBIOS UUID a template is made with.
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
# A template's name, as Proxmox takes a guest's: a DNS name.
NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?")
# The CPU type and OS type of a guest, as qm takes them.
CPU = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}")
OSTYPE = re.compile(r"[a-z0-9]{1,16}")
# The guest agent on, as today's role turns it on: what pve/template.py's AGENT reads back as.
AGENT = "enabled=1"
# Where libguestfs runs: its own appliance, as today's role runs it, never through libvirt.
GUESTFS = ("env", "LIBGUESTFS_BACKEND=direct")
# apt's, with no question asked.
APT = ("env", "DEBIAN_FRONTEND=noninteractive", "apt-get")


def _id(guest: object) -> str:
    if isinstance(guest, bool) or not isinstance(guest, int) or not LOWEST <= guest <= HIGHEST:
        raise ValueError(f"a guest's ID is a whole number from {LOWEST} to {HIGHEST}")
    return str(guest)


def _path(path: object, what: str) -> str:
    """An absolute path of plain parts, with no . or .. part and no empty one."""
    parts = path.split("/") if isinstance(path, str) else []
    if len(parts) < 2 or parts[0] != "" or not all(PART.fullmatch(part) for part in parts[1:]):
        raise ValueError(f"{what} is an absolute path of plain characters, with no . or .. part")
    return path


def _named(value: object, kind: str) -> str:
    if not isinstance(value, str) or not proxmox_name(value, kind):
        raise ValueError(f"the {kind} is named as Proxmox names one")
    return value


def _held(value: object, pattern: re.Pattern, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{what} is not of its form")
    return value


def _count(value: object, what: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{what} is a whole number, 1 or more")
    return str(value)


def config(node: str, guest: int) -> list[str]:
    """The guest's configuration as JSON, from the node that holds it."""
    return ["pvesh", "get", f"/nodes/{_named(node, 'node')}/qemu/{_id(guest)}/config", "--output-format", "json"]


def folder(path: str) -> list[str]:
    """The image directory made, with its parents, where it is not there."""
    return ["mkdir", "-p", _path(path, "the image directory")]


def digest_of(path: str) -> list[str]:
    """The file's SHA-256, as the host's own sha256sum gives it: its first word."""
    return ["sha256sum", _path(path, "the image")]


def fetch(url: str, path: str) -> list[str]:
    """The image fetched by the host's own curl into path: a failure is an exit status, a redirect followed."""
    return [
        "curl",
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--output",
        _path(path, "the image's download"),
        _held(url, URL, "the image's URL"),
    ]


def put_in_place(part: str, path: str) -> list[str]:
    """The download renamed to the image's name, once its digest is the one verified."""
    return ["mv", "-f", _path(part, "the image's download"), _path(path, "the image")]


def removed(path: str) -> list[str]:
    """A file of the build's removed, where it is there."""
    return ["rm", "-f", _path(path, "a file of the build")]


def copied(source: str, path: str) -> list[str]:
    """The image copied for the image tool to change, so that the download stays as it was fetched."""
    return ["cp", _path(source, "the image"), _path(path, "the image's copy")]


def os_release(image: str) -> list[str]:
    """The image's os-release, read by the image tool without starting it."""
    return [*GUESTFS, "virt-cat", "-a", _path(image, "the image"), OS_RELEASE]


def customised(image: str, packages: Sequence[str]) -> list[str]:
    """The copy changed by the image tool: the packages installed into it, and its machine ID emptied, so that clones
    that share none send no one DHCP client identifier and are never handed one lease."""
    if not packages:
        raise ValueError("an image is customised with the packages put into it")
    names = [_held(package, PACKAGE, "a package's name") for package in packages]
    path = _path(image, "the image's copy")
    return [*GUESTFS, "virt-customize", "--add", path, "--install", ",".join(names), "--truncate", "/etc/machine-id"]


def installed(package: str) -> list[str]:
    """Whether a package is installed, as dpkg says it: `install ok installed`, on a line of its own, since the runner
    takes no line with no end."""
    return ["dpkg-query", "--show", "--showformat=${Status}\\n", _held(package, PACKAGE, "a package's name")]


def update() -> list[str]:
    """apt's lists, read again before anything is installed."""
    return [*APT, "update"]


def install(packages: Sequence[str]) -> list[str]:
    """The packages installed by apt, with no question asked."""
    if not packages:
        raise ValueError("apt installs at least one package")
    return [*APT, "install", "--yes", *(_held(package, PACKAGE, "a package's name") for package in packages)]


def destroy(guest: int) -> list[str]:
    """The guest removed, which Proxmox refuses for a template a linked clone uses."""
    return ["qm", "destroy", _id(guest)]


def create(
    guest: int,
    *,
    name: str,
    ostype: str,
    cpu: str,
    cores: int,
    memory: int,
    bridge: str,
    uuid: str,
    notes: str,
    pool: str,
) -> list[str]:
    """qm create of the template at guest, with today's role's settings, the SMBIOS UUID made for it, the record as
    its description (notes, pve/record.py's render) and the fleet's pool, of which it is a member from the start."""
    if not isinstance(notes, str) or not notes or notes.startswith("-"):
        raise ValueError("a template's notes are its record, as pve/record.py renders it")
    return [
        "qm",
        "create",
        _id(guest),
        "--name",
        _held(name, NAME, "the template's name"),
        "--ostype",
        _held(ostype, OSTYPE, "the template's OS type"),
        "--cpu",
        _held(cpu, CPU, "the template's CPU type"),
        "--cores",
        _count(cores, "the template's cores"),
        "--memory",
        _count(memory, "the template's memory"),
        "--net0",
        f"virtio,bridge={_named(bridge, 'bridge')}",
        "--scsihw",
        SCSIHW,
        "--agent",
        AGENT,
        "--serial0",
        SERIAL0,
        "--vga",
        VGA,
        "--smbios1",
        f"uuid={_held(uuid, UUID, 'the template UUID')}",
        "--description",
        notes,
        "--pool",
        _named(pool, "pool"),
    ]


def import_disk(guest: int, storage: str, image: str) -> list[str]:
    """The image imported as the template's boot disk, on storage."""
    at = f"{_named(storage, 'storage')}:0,import-from={_path(image, 'the image to import')},discard=on"
    return ["qm", "set", _id(guest), "--scsi0", at]


def cloud_init(guest: int, storage: str) -> list[str]:
    """The cloud-init drive, the boot order and DHCP: no ciupgrade, which leaves Proxmox's default."""
    drive = f"{_named(storage, 'storage')}:cloudinit"
    return ["qm", "set", _id(guest), "--ide2", drive, "--boot", "order=scsi0", "--ipconfig0", "ip=dhcp"]


def template(guest: int) -> list[str]:
    """The guest made a template."""
    return ["qm", "template", _id(guest)]
