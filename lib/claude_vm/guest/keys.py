r"""A person's SSH keys on a machine's account: added by layer 1 alone, and one taken off by its fingerprint
(docs/architecture/engine.md §16.10).

Adding is the system play's: `common`'s keys tasks (roles/common/tasks/keys.yml) put each key of `vm_ssh_public_keys`
on the account and leave every other line there, so a key that is on a machine and not in the list stays (the list
only adds); they find a listed key where sshd reads a line's key, as `pattern` here does. Nothing the play reaches
takes a key off `authorized_keys` or writes the file whole (tests/unit/test_aw_keys.py holds it, its patterns rendered
over other keys' lines), and nothing of the engine but the create's settings call sends Proxmox's
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
the run may be using; and a fingerprint that matches no line. What ssh offers is asked of ssh itself, for each machine:
`ssh -G` with the machine's whole line (runner.shown: the product's options, `vm_ssh_options`, the person's own
`~/.ssh/config`), whose `identityagent` (a path, `${VAR}`, ssh's older `$VAR`, else the run's `SSH_AUTH_SOCK`) is asked
for its keys over the agent protocol, and whose every `identityfile` is read by its public half (`<file>.pub`, or the
file itself where it is one). An identity file that is there but whose public half cannot be read, one named with a
token this module does not expand, and a `pkcs11provider`, whose token's keys this run cannot list, refuse the key too:
this run cannot tell it is not the one in use (`offered_by`).

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
# The names of an RSA key's SHA-2 signatures, which sshd reads as its type too where the blob is ssh-rsa's.
RSA_SHA2 = frozenset({"rsa-sha2-256", "rsa-sha2-512"})
# The agent protocol's request of its identities, and its answer's type.
AGENT_REQUEST = 11
AGENT_ANSWER = 12

# A key's type and its base64, as authorized_keys gives them (common_ssh_key_pattern's two groups); sshd also reads an
# RSA key's blob after the names of its two SHA-2 signatures (RSA_SHA2).
_TYPE = r"(?:ssh|ecdsa|sk)-[!-~]+|rsa-sha2-(?:256|512)"
_DATA = r"AAAA[0-9A-Za-z+/]+={0,2}"
# Options in front of a key, as sshd skips them (sshkey_advance_past_options): one word to the first blank outside
# double quotes, where a backslash escapes a double quote alone, inside quotes and out, and is a plain character before
# anything else. roles/common/vars/main.yml's common_ssh_key_options is the same (tests/unit/test_aw_keys.py).
_OPTIONS = r'(?:\\"|\\(?!")|[^ \t"\n\\]|"(?:\\"|\\(?!")|[^"\n\\])*")+'
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
    named = blob[4 : 4 + size]
    if named == kind.encode("ascii") or (kind in RSA_SHA2 and named == b"ssh-rsa"):
        return Key(kind, data)
    return None


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
    """The key a public key file beside an identity file holds (`<file>.pub`, or the file itself where it is one), or
    None."""
    for each in (path.with_name(path.name + ".pub"), path):
        try:
            with open(each, encoding="utf-8", errors="replace") as found:
                text = found.read(PUBLIC_MOST)
        except OSError:
            continue
        lines = text.splitlines()
        key = parsed(lines[0]) if lines else None
        if key is not None:
            return key
    return None


_TOKEN = re.compile(r"%(.)|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def shown_options(lines: Iterable[str]) -> dict[str, list[str]]:
    """ssh -G's lines as each option's values, by its name in lower case, in their order."""
    found: dict[str, list[str]] = {}
    for line in lines:
        name, _, value = str(line).strip().partition(" ")
        if name:
            found.setdefault(name.lower(), []).append(value.strip())
    return found


def _resolved(text: str, env: Mapping[str, str], shown: Mapping[str, Sequence[str]]) -> str | None:
    """A path ssh -G shows, its ~ and the tokens ssh expands in an identity file or agent made what ssh makes them:
    None where a token is one this module does not expand, or names a variable the run's environment does not hold."""
    home = env.get("HOME") or ""

    def first(name: str) -> str:
        return (list(shown.get(name) or ()) or [""])[0]

    tokens = {
        "%": "%",
        "d": home,
        "u": env.get("USER") or env.get("LOGNAME") or "",
        "h": first("hostname"),
        "n": first("hostname"),
        "r": first("user"),
        "p": first("port"),
    }
    unknown = False

    def put(found: re.Match) -> str:
        nonlocal unknown
        value = tokens.get(found[1]) if found[1] is not None else env.get(found[2])
        if not value:
            unknown = True
            return ""
        return value

    made = _TOKEN.sub(put, text)
    if unknown or (made.startswith("~") and not (made == "~" or made.startswith("~/"))):
        return None
    return str(_expanded(made, home))


@dataclasses.dataclass(frozen=True)
class Offered:
    """What this computer's ssh offers a machine: each key's fingerprint, and each identity this run could not read,
    said."""

    fingerprints: frozenset[str]
    unread: tuple[str, ...] = ()


def offered_by(
    env: Mapping[str, str], shown: Mapping[str, Sequence[str]] | None = None, identity_file: Path | str | None = None
) -> Offered:
    """What this computer's ssh offers a machine. With ssh -G's options for it (shown_options): the agent its
    identityagent names (none for `none`, the run's SSH_AUTH_SOCK for `SSH_AUTH_SOCK` or where none is shown) and each
    identityfile's public half. Without them, as far as the run's own environment says: the agent at SSH_AUTH_SOCK, the
    identity file a target names, and ssh's default identity files under HOME. An identity file that is there whose
    public half cannot be read, and one or an agent named by a token this module does not expand, are said as unread."""
    home = env.get("HOME") or ""
    unread: list[str] = []
    if shown is None:
        agent: str | None = env.get("SSH_AUTH_SOCK")
        files = [_expanded(identity_file, home)] if identity_file else []
        if home:
            files += [Path(home) / ".ssh" / name for name in DEFAULT_IDENTITIES]
    else:
        named = list(shown.get("identityagent") or ())
        agent = env.get("SSH_AUTH_SOCK")
        if named and named[0].lower() == "none":
            agent = None
        elif named and named[0].startswith("$") and not named[0].startswith("${"):
            # ssh's older form: the name of a variable of the environment that holds the socket, which ssh -G shows
            # as it is given and ssh reads only as it connects; no agent where the run's environment holds none.
            agent = env.get(named[0][1:]) or None
        elif named and named[0] != "SSH_AUTH_SOCK":
            agent = _resolved(named[0].strip('"'), env, shown)
            if agent is None:
                unread.append(f"the agent {named[0]}")
        # A PKCS#11 provider, which only the person's own ~/.ssh/config may name (runner refuses it in
        # vm_ssh_options): ssh offers every key its token holds, which this run cannot list.
        for provider in shown.get("pkcs11provider") or ():
            if provider.strip('"').lower() != "none":
                unread.append(f"the keys of the PKCS#11 provider {provider}")
        files = []
        for raw in [str(identity_file)] * bool(identity_file) + list(shown.get("identityfile") or ()):
            path = _resolved(raw.strip('"'), env, shown)
            if path is None:
                unread.append(f"the identity file {raw}")
            else:
                files.append(Path(path))
    found = {fingerprint_of(blob) for blob in agent_keys(agent)}
    for path in files:
        key = _public_half(path)
        if key is not None:
            found.add(key.fingerprint)
        elif path.exists():
            unread.append(f"the identity file {path}, whose public half {path}.pub could not be read")
    return Offered(frozenset(found), tuple(dict.fromkeys(unread)))


def offered(
    env: Mapping[str, str], identity_file: Path | str | None = None, shown: Mapping[str, Sequence[str]] | None = None
) -> frozenset[str]:
    """The fingerprint of each key this computer's ssh offers a machine (offered_by)."""
    return offered_by(env, shown, identity_file).fingerprints


def refusal(
    fingerprint: str,
    listed: Iterable[object],
    env: Mapping[str, str],
    identity_file: Path | str | None = None,
    shown: Mapping[str, Sequence[str]] | None = None,
) -> str | None:
    """Why the key of fingerprint may not be taken off, in a sentence, or None: one vm_ssh_public_keys lists (listed),
    which the next configure would put back, or one this computer's ssh offers (offered_by), which the run may be
    using, or where ssh offers an identity this run cannot read."""
    named = {key.fingerprint for line in listed if isinstance(line, str) and (key := parsed(line)) is not None}
    if fingerprint in named:
        return (
            f"the key {fingerprint} is in vm_ssh_public_keys, which every configure puts on each VM again: take it out"
            " of that setting first, then take it off"
        )
    found = offered_by(env, shown, identity_file)
    if fingerprint in found.fingerprints:
        return (
            f"the key {fingerprint} is one this computer's ssh offers each VM (its agent holds it, or it is an"
            " identity file's), so this run may be the one using it, and taking it off could leave this computer no"
            " way in: take it off from another computer"
        )
    if found.unread:
        return (
            f"this computer's ssh offers each VM {'; '.join(found.unread)}, so this run cannot tell that the key"
            f" {fingerprint} is not the one it is using: put that key's .pub beside it, or take the key off from"
            " another computer"
        )
    return None


# ---------------------------------------------------------------- on a machine ----


async def _read(job: Job, target: runner.Target, env: Mapping[str, str], name: str) -> list[str]:
    result = await runner.run(job, target, ["cat", "--", AUTHORIZED], None, READ, dict(env))
    if isinstance(result, runner.Done) and result.status == 0:
        return list(result.lines)
    why = getattr(result, "said", "") or "cat did not answer"
    raise Failed(f"~/{AUTHORIZED} on {name} could not be read: {' '.join(why.split())[:300]}")


async def _shown(job: Job, target: runner.Target, env: Mapping[str, str], name: str) -> dict[str, list[str]]:
    """ssh -G's options for target (runner.shown), or Failed with ssh's words."""
    result = await runner.shown(job, target, dict(env))
    if isinstance(result, runner.Done) and result.status == 0:
        return shown_options(result.lines)
    why = getattr(result, "said", "") or "no answer"
    raise Failed(
        f"this computer's ssh could not say which keys it offers {name} (ssh -G): {' '.join(why.split())[:300]}"
    )


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
    if why is None:
        why = refusal(fingerprint, (), env, None, await _shown(job, target, env, name))
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
    # Every secret the run holds struck from what the child says: the job's redaction list (layer1.run).
    job.redact(*redacted)
    ended = await layer1.run(job, target, PLAY, written, name=name, host_flag=host_flag, env=dict(env))
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
