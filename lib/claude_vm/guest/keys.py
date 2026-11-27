r"""A person's SSH keys on a machine's account: added by layer 1 alone, and one taken off by its fingerprint
(docs/architecture/engine.md §16.10).

Adding is the system play's: `common`'s keys tasks (roles/common/tasks/keys.yml) put each key of `vm_ssh_public_keys`
on the account and leave every other line there, so a key that is on a machine and not in the list stays (the list
only adds). Nothing else the play reaches takes a line of `authorized_keys` away or writes the file whole
(tests/unit/test_aw_keys.py holds it), and nothing of the engine but the create's settings call sends Proxmox's
`sshkeys`: a changed `sshkeys` makes a living VM's next start a new cloud-init instance.

Taking one off is an act of its own, `deploy --only configure --remove-key <fingerprint>`, never the side effect of two
lists that differ. `remove(job, target, fingerprint, ...)` reads the account's `~/.ssh/authorized_keys` through the
runner, finds each line whose key has that SHA256 fingerprint (`parsed`: a line commented out is no key's; options in
front are skipped as sshd skips them, a quoted one whole), and takes those lines off with `ansible.builtin.lineinfile`,
`state: absent`, in a one-task play of its own (`PLAY`) run by layer 1's child (layer1.run), as the account and never
as root, so that it reaches only what the account could change already. Its `regexp` is the key's type and base64 at
the place sshd reads a key (`pattern`): after options a quoted string keeps whole, so a line of another key whose
option or comment names this key's base64 is not one of its lines; within one line (`[ \t]`, never `\s`). It reaches
the play as a variable of the play's variables file (guest/variables.py), which no template reads, so its backslashes
reach Python's re as written. lineinfile writes through a linked `~/.ssh` and keeps the file's mode, as the account
left them. Then the file is read again, and it must be what it was less those lines, or the run says so.

Refused with a sentence, before anything is changed (`Refused`): a fingerprint that is not one; a key
`vm_ssh_public_keys` lists, which the next configure would put back; a key this computer's ssh offers a machine, which
the run may be using (`offered`: each key the agent at the run's `SSH_AUTH_SOCK` holds, the public half of the identity
file the target names, and of each of ssh's default identity files under the run's `HOME`); and a fingerprint that
matches no line. An identity file or agent that only the person's own ~/.ssh/config names is not read here.

Standard library only; asyncio, socket and layer1 are imported inside the functions that need them.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import re
import struct
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from claude_vm.guest import variables as play_variables
from claude_vm.secret import Secret
from claude_vm.ssh import runner

if TYPE_CHECKING:
    from claude_vm.runs.job import Job

# The one-task play that takes a key's lines off, in a folder of its own beside this module: nothing else is beside
# it for Ansible to read (a group_vars/, a library/). And the one name its variables hold: the key's pattern.
PLAY = Path(__file__).resolve().parent / "plays" / "remove_key.yml"
PATTERN_VARIABLE = "remove_key_pattern"
NAMES = frozenset({PATTERN_VARIABLE})
# The account's keys file, from its home, where a command over SSH starts.
AUTHORIZED = ".ssh/authorized_keys"
# A fingerprint as ssh-keygen -l prints it: SHA256: and the digest's 43 characters of base64, unpadded.
FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
# One read of the keys file: a few seconds' work.
READ = runner.Limits(seconds=30.0, silent=None)
# ssh's default identity files, under ~/.ssh, whose public halves (<name>.pub) are read.
DEFAULT_IDENTITIES = ("id_rsa", "id_ecdsa", "id_ecdsa_sk", "id_ed25519", "id_ed25519_sk", "id_dsa")
# How long the agent is given to answer, and the most of its answer read; the most of a public key file read.
AGENT_SECONDS = 2.0
AGENT_MOST = 256 * 1024
PUBLIC_MOST = 64 * 1024
# The agent protocol's request of its identities, and its answer's type.
AGENT_REQUEST = 11
AGENT_ANSWER = 12

# A key's type and its base64, as authorized_keys gives them (common_ssh_key_pattern's two groups).
_TYPE = r"(?:ssh|ecdsa|sk)-[!-~]+"
_DATA = r"AAAA[0-9A-Za-z+/]+={0,2}"
# Options in front of a key: one word of characters that are no blank, or strings in double quotes, each kept whole
# with a backslash's escape inside it, as sshd reads them.
_OPTIONS = r'(?:[^ \t"\n]|"(?:[^"\\\n]|\\.)*")+'
_BARE = re.compile(rf"(?P<type>{_TYPE})[ \t]+(?P<data>{_DATA})(?:[ \t]|\Z)")
_OPTIONED = re.compile(rf"{_OPTIONS}[ \t]+(?P<type>{_TYPE})[ \t]+(?P<data>{_DATA})(?:[ \t]|\Z)")


class Refused(ValueError):
    """A key not taken off, said by why: nothing was changed on the machine."""


class Failed(Exception):
    """The play that takes a key off failed, or the keys file is not what it should be after it: the sentence."""


@dataclasses.dataclass(frozen=True)
class Key:
    """A public key: its type and its base64, which decodes to a blob that names that type first."""

    type: str
    data: str

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(base64.b64decode(self.data))


@dataclasses.dataclass(frozen=True)
class Removed:
    """A key taken off a machine's account: its fingerprint, and how many lines held it."""

    fingerprint: str
    lines: int


def fingerprint_of(blob: bytes) -> str:
    """A key's SHA256 fingerprint, as ssh-keygen -l prints it, from its blob."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")


def _key(kind: str, data: str) -> Key | None:
    """The key of type kind whose base64 is data, where data decodes to a blob that names kind first."""
    try:
        blob = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(blob) < 4:
        return None
    (size,) = struct.unpack(">I", blob[:4])
    return Key(kind, data) if blob[4 : 4 + size] == kind.encode("ascii") else None


def parsed(line: str) -> Key | None:
    """The key a line of authorized_keys authorises, or None: a blank line, one commented out, or one with no key. A
    key at the line's start is read first, then one after its options, as sshd reads them."""
    text = line.rstrip("\r\n").lstrip(" \t")
    if not text or text.startswith("#"):
        return None
    for found in (_BARE.match(text), _OPTIONED.match(text)):
        if found is not None and (key := _key(found["type"], found["data"])) is not None:
            return key
    return None


def pattern(*keys: Key) -> str:
    """The regexp of lineinfile's that finds the lines of keys, one key written in one or more ways: a line not
    commented out whose key, at its start or after its options, is a key's type and base64, then a blank or the line's
    end; within the one line."""
    kinds = "|".join(sorted({re.escape(key.type) for key in keys}))
    datas = "|".join(sorted({re.escape(key.data) for key in keys}))
    return rf"^[ \t]*(?!#)(?:{_OPTIONS}[ \t]+)?(?:{kinds})[ \t]+(?:{datas})(?:[ \t].*)?\r?$"


def held(fingerprint: object) -> str:
    """fingerprint held to what ssh-keygen -l prints, else Refused."""
    if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
        raise Refused(
            "a key is taken off by its fingerprint, the second word ssh-keygen -l prints: SHA256: and 43 characters"
            " of base64"
        )
    return fingerprint


# ---------------------------------------------------------------- what this computer's ssh offers ----


def _exactly(connection, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ValueError("the agent's answer ended early")
        data += chunk
    return data


def _strings(body: bytes, at: int, count: int) -> tuple[list[bytes], int]:
    found = []
    for _ in range(count):
        if at + 4 > len(body):
            raise ValueError("the agent's answer ended early")
        (size,) = struct.unpack(">I", body[at : at + 4])
        if at + 4 + size > len(body):
            raise ValueError("the agent's answer ended early")
        found.append(body[at + 4 : at + 4 + size])
        at += 4 + size
    return found, at


def agent_keys(socket_path: object) -> tuple[bytes, ...]:
    """The blob of each key the agent at socket_path holds, asked over the agent protocol; none where there is no
    agent, or it does not answer as an agent does within AGENT_SECONDS."""
    if not isinstance(socket_path, str) or not socket_path:
        return ()
    import socket

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(AGENT_SECONDS)
            connection.connect(socket_path)
            connection.sendall(struct.pack(">IB", 1, AGENT_REQUEST))
            (size,) = struct.unpack(">I", _exactly(connection, 4))
            if not 5 <= size <= AGENT_MOST:
                return ()
            body = _exactly(connection, size)
        if body[0] != AGENT_ANSWER:
            return ()
        (count,) = struct.unpack(">I", body[1:5])
        blobs, at = [], 5
        for _ in range(count):
            (blob, _comment), at = _strings(body, at, 2)
            blobs.append(blob)
        return tuple(blobs)
    except (OSError, ValueError, struct.error):
        return ()


def _expanded(path: Path | str, home: str) -> Path:
    text = str(path)
    if home and (text == "~" or text.startswith("~/")):
        return Path(home) / text[2:]
    return Path(text)


def _public_half(path: Path) -> Key | None:
    """The key a public key file beside an identity file holds, or None."""
    try:
        with open(path.with_name(path.name + ".pub"), encoding="utf-8", errors="replace") as found:
            text = found.read(PUBLIC_MOST)
    except OSError:
        return None
    lines = text.splitlines()
    return parsed(lines[0]) if lines else None


def offered(env: Mapping[str, str], identity_file: Path | str | None = None) -> frozenset[str]:
    """The fingerprint of each key this computer's ssh offers a machine, as far as the run's own environment says: the
    agent's at SSH_AUTH_SOCK, the identity file a target names, and ssh's default identity files under HOME."""
    found = {fingerprint_of(blob) for blob in agent_keys(env.get("SSH_AUTH_SOCK"))}
    home = env.get("HOME") or ""
    files = [_expanded(identity_file, home)] if identity_file else []
    if home:
        files += [Path(home) / ".ssh" / name for name in DEFAULT_IDENTITIES]
    for path in files:
        key = _public_half(path)
        if key is not None:
            found.add(key.fingerprint)
    return frozenset(found)


def refusal(
    fingerprint: str, listed: Iterable[object], env: Mapping[str, str], identity_file: Path | str | None = None
) -> str | None:
    """Why the key of fingerprint may not be taken off, in a sentence, or None: one vm_ssh_public_keys lists (listed),
    which the next configure would put back, or one this computer's ssh offers (offered), which the run may be using."""
    named = {key.fingerprint for line in listed if isinstance(line, str) and (key := parsed(line)) is not None}
    if fingerprint in named:
        return (
            f"the key {fingerprint} is in vm_ssh_public_keys, which every configure puts on each VM again: take it out"
            " of that setting first, then take it off"
        )
    if fingerprint in offered(env, identity_file):
        return (
            f"the key {fingerprint} is one this computer's ssh offers each VM (its agent holds it, or it is an"
            " identity file's), so this run may be the one using it, and taking it off could leave this computer no"
            " way in: take it off from another computer"
        )
    return None


# ---------------------------------------------------------------- on a machine ----


async def _read(job: Job, target: runner.Target, env: Mapping[str, str], name: str) -> list[str]:
    result = await runner.run(job, target, ["cat", "--", AUTHORIZED], None, READ, dict(env))
    if isinstance(result, runner.Done) and result.status == 0:
        return list(result.lines)
    why = getattr(result, "said", "") or "cat did not answer"
    raise Failed(f"~/{AUTHORIZED} on {name} could not be read: {' '.join(why.split())[:300]}")


def _last(text: str) -> str:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    return " ".join(lines[-1].split())[:600] if lines else "it said nothing"


async def remove(
    job: Job,
    target: runner.Target,
    fingerprint: str,
    *,
    name: str,
    host_flag: str,
    env: Mapping[str, str],
    listed: Sequence[object] = (),
    redacted: Sequence[Secret] = (),
) -> Removed:
    """The key of fingerprint taken off target's account: each line of ~/.ssh/authorized_keys that holds it, by a
    one-task play of layer 1's, every other line left as it was. Refused, before anything is changed, where the key is
    one listed (vm_ssh_public_keys), one this computer's ssh offers, or on no line; Failed where the play failed or the
    file is not what it was less those lines. name is the name a person chose for the machine, host_flag the host a
    line names (the printer's), env the run's environment, redacted every secret the run holds."""
    from claude_vm.guest import layer1
    from claude_vm.runs import rundir

    fingerprint = held(fingerprint)
    why = refusal(fingerprint, listed, env, target.identity_file)
    if why is not None:
        raise Refused(why)
    before = await _read(job, target, env, name)
    keys = [parsed(line) for line in before]
    found = {at for at, key in enumerate(keys) if key is not None and key.fingerprint == fingerprint}
    if not found:
        raise Refused(f"no line of ~/{AUTHORIZED} on {name} holds the key {fingerprint}, so nothing was taken off")
    path = rundir.path_for(job, f"remove-key-{target.identity}.json")
    regexp = pattern(*(key for at, key in enumerate(keys) if at in found and key is not None))
    written = play_variables.write(path, {PATTERN_VARIABLE: regexp}, secrets=redacted, names=NAMES)
    ended = await layer1.run(
        job, target, PLAY, written, name=name, host_flag=host_flag, env=dict(env), redacted=redacted
    )
    if ended.status != 0 or ended.counts.failed or ended.counts.unreachable or not ended.counts.recap:
        raise Failed(
            f"taking the key {fingerprint} off {name} failed (layer 1's exit status {ended.status}):"
            f" {_last(ended.said)}"
        )
    after = await _read(job, target, env, name)
    if after != [line for at, line in enumerate(before) if at not in found]:
        raise Failed(
            f"~/{AUTHORIZED} on {name} is not what taking the key {fingerprint} off leaves: the run stopped there,"
            " so read the file on the VM"
        )
    return Removed(fingerprint, len(found))
