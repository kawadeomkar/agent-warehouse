"""The API's certificate, worked out before any request that carries a credential (docs/architecture/engine.md §16.4).

Before init sends a Proxmox host a token or a password, `work_out(host, given, root_ssh, ask)` settles what its API's
certificate is held to, by probes that carry no credential - today's lib/claude_vm/trust.py's, as just init makes
them - and returns what the host keeps from then on (`Kept`), or why nothing that carries a credential may be sent
(`Refusal`). In this order:

1. What checking uses now. The anchor the host keeps (fleet/hosts.py), its name checked unless a fingerprint is
   recorded beside it, which the certificate presented must then have, as the client holds it (pve/pin.py); or, where
   it keeps none, this computer's certificates, certifi's list with the file tls_ca_file names added
   (vendors/trust.py), handed to the probe as their text, so that trust.py's own idea of a store, which a variable of
   the environment names, is never asked. Where that verifies, nothing changes: a host the computer's certificates
   verify keeps no anchor, and one its CA renews still verifies. A fingerprint given (`--certificate`) is compared
   there too, with the kept anchor's own or, where the host keeps none, with what the address presents: a match is
   said, and a mismatch is a refusal that names both.
2. Otherwise the host's own certificate files, read over root SSH (`read_over_root_ssh`, each file looked at before it
   is read): its CA where the certificate presented chains to it, its name checked (kind `ca`, no fingerprint, so that
   the CA's renewals verify); else the node's own certificate without its chain (kind `node`), its fingerprint
   recorded beside it. Where neither verifies at the address and what it presents is the host's own under another
   name - the node's own certificate, byte for byte, or one the host's CA signed, and nothing else - that certificate
   is kept as the node's own, held to its fingerprint and not to a name, which B22's client then reaches whatever
   address it is given. The node's file is read for its first certificate alone: a custom one is served with its
   chain, and an intermediate of it taken for an anchor would pass whatever it issued, for any name, for the host's
   own. Where what it presents is none of the host's, or nothing, the refusal is `token_held`: something other than
   the host answers there.
3. Where root SSH does not answer, is not tried (`root_ssh` None), or reads no certificate, nothing on the host vouches
   for what the address presents: its fingerprint is shown, and it is kept as the node's own only on the person's yes,
   or where it is the one `--certificate` gives.

An anchor kept is put in the place of one that no longer verifies only on a yes that shows both fingerprints, or where
the new one is the one given; with nobody there (`ask` None: `--no-prompt`) and none given, what only a person can
vouch for is a refusal that names `--certificate`. No sentence says to check less: there is no unchecked mode. Every
refusal means the caller sends nothing that carries a credential; `token_held` says the reason is what answers.

What is returned is what pve/pin.py's `kept` takes, made through it once before it is returned: no context is built
here but through pin, nothing here reads trust.py's `_store_files`, and the probes' contexts are trust.py's. The caller
keeps an anchor through fleet/hosts.py's one writer, `write`, with its kind and fingerprint in one write. Standard
library, trust.py's probes, fleet/, pve/pin.py, ssh/runner.py and the printer at import; asyncio inside the functions
that wait.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..fleet import hosts
from ..pve import pin
from ..schema.printer import said
from ..schema.words import Line
from ..ssh import runner
from ..text import one_line
from ..trust import (
    _probe,
    pem_certificates,
    pem_fingerprint,
    pem_first,
    proxmox_trust_pick,
    proxmox_trust_presented,
    proxmox_trust_shown,
)
from ..vendors import trust as vendors_trust

if TYPE_CHECKING:
    from ..runs.job import Job

# The host's certificate files, read over root SSH in this order: Proxmox's CA, and the node's certificate pveproxy
# serves - a custom or ACME one where that is there, else the one Proxmox made - as today's init reads them.
HOST_FILES: Mapping[str, str] = MappingProxyType(
    {
        "ca": "/etc/pve/pve-root-ca.pem",
        "custom": "/etc/pve/local/pveproxy-ssl.pem",
        "node": "/etc/pve/local/pve-ssl.pem",
    }
)
# A file larger than a certificate file is no certificate file, and is never read whole (trust.py's bound).
MOST = 65536
# How long each probe waits for the host, in seconds.
TIMEOUT = 10.0
# How much of the computer's certificate files is read for the probe: certifi's list is about 300 KiB.
STORE_MOST = 16 * 1024 * 1024
# What reading the host's files over root SSH may take: one line a file, its base64, and the time today's run gives.
LIMITS = runner.Limits(line=2 * MOST, most=8 * MOST, seconds=runner.SECONDS)
# Run by the host's shell for each file named after it: one line, "-" where it is not a file of at most MOST bytes,
# else "+" and its bytes in base64 on that line alone. The size is looked at before anything is read.
READ = (
    "for f do\n"
    f'  if [ -f "$f" ] && n=$(wc -c < "$f" 2>/dev/null | tr -d " ") && [ "$n" -le {MOST} ]; then\n'
    "    printf +; base64 < \"$f\" | tr -d '\\n'; echo\n"
    "  else\n"
    "    echo -\n"
    "  fi\n"
    "done"
)
# A certificate of the computer's certificate files, as PEM armours it: the blocks the probe of a host with no anchor is
# given, and nothing between them.
STORE_PEM = re.compile(r"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]{64,65536}?-----END CERTIFICATE-----")
# What a person runs on the host to see a certificate's fingerprint, to compare with the one shown.
COMPARE = "openssl x509 -noout -fingerprint -sha256 -in"
# How each kind of anchor is said.
KINDS = {"ca": "its CA", "node": "the node's own certificate"}


@dataclasses.dataclass(frozen=True)
class Host:
    """What the certificate's step knows of a host: its name, the address and port its API is reached at, and what it
    keeps now - its anchor's text (None for none), the anchor's kind and the fingerprint recorded beside it - with this
    computer's setting tls_ca_file, read only where it keeps no anchor."""

    name: str
    address: str
    api_port: int = 8006
    anchor: str | None = None
    trust_kind: str | None = None
    fingerprint: str = ""
    tls_ca_file: str | None = None

    @classmethod
    def of(cls, name: str, fields: hosts.HostFile, anchor: str | None, tls_ca_file: str | None = None) -> Host:
        """The host as its folder keeps it (fleet/hosts.py's fields and anchor)."""
        return cls(
            name=name,
            address=fields.address,
            api_port=fields.api_port,
            anchor=anchor,
            trust_kind=fields.trust_kind,
            fingerprint=fields.fingerprint,
            tls_ca_file=tls_ca_file,
        )

    @property
    def endpoint(self) -> str:
        """The address and port, as a URL has them: an IPv6 address in brackets."""
        return f"[{self.address}]:{self.api_port}" if ":" in self.address else f"{self.address}:{self.api_port}"

    @property
    def url(self) -> str:
        return f"https://{self.endpoint}/api2/json"

    @property
    def anchor_said(self) -> str:
        """The host's anchor's file, as a sentence names it."""
        return f"{hosts.HOSTS_REL}/{self.name}/{hosts.ANCHOR}"

    @property
    def host_file_said(self) -> str:
        return f"{hosts.HOSTS_REL}/{self.name}/{hosts.HOST_FILE}"


@dataclasses.dataclass(frozen=True)
class Read:
    """What root SSH read of the host's certificate files: its CA's, and the node's (its custom one's where there is
    one), each the file's text, "" for one not there or larger than a certificate file is."""

    ca: str = ""
    node: str = ""


class NoRootSsh(Exception):
    """Root SSH to the host did not answer, or not as asked: why, in plain words."""


@dataclasses.dataclass(frozen=True)
class Question:
    """What a person is shown, line by line, before the yes or no the prompt asks."""

    lines: tuple[str, ...]
    prompt: str


@dataclasses.dataclass(frozen=True)
class Kept:
    """What the host is held to from now on: the anchor to keep (fleet/hosts.py's Anchor), or None for none, where the
    computer's certificates verify it; whether that differs from what it keeps now, and so is to be written; and what is
    said of it."""

    anchor: hosts.Anchor | None
    changed: bool
    said: str


@dataclasses.dataclass(frozen=True)
class Refusal:
    """Nothing that carries a credential may be sent, and why. token_held: what answers at the address is not shown to
    be the host, though root SSH read the host's certificates."""

    said: str
    token_held: bool = False


Ask = Callable[[Question], Awaitable[bool]]
RootSsh = Callable[[], Awaitable[Read]]


# ---------------------------------------------------------------- the host's files over root SSH ----


def read_command(files: Mapping[str, str] = HOST_FILES) -> list[str]:
    """The command that reads files on the host, one line each in their order: the shell, READ, and each path as an
    argument of its own. A path is absolute, one line."""
    for path in files.values():
        if not path.startswith("/") or any(ord(char) < 32 for char in path):
            raise ValueError("a file read on the host is an absolute path of one line")
    return ["sh", "-c", READ, "sh", *files.values()]


def parse(lines: Sequence[str], files: Mapping[str, str] = HOST_FILES) -> Read:
    """What READ printed for files, as a Read: NoRootSsh where it is not one line a file, each "-" or "+" and base64 of
    at most MOST bytes. A file that is no ASCII text holds no certificate."""
    if len(lines) != len(files):
        raise NoRootSsh("it did not answer as asked")
    texts: dict[str, str | None] = {}
    for name, line in zip(files, lines, strict=True):
        if line == "-":
            texts[name] = None
            continue
        if not line.startswith("+"):
            raise NoRootSsh("it did not answer as asked")
        try:
            data = base64.b64decode(line[1:], validate=True)
        except (binascii.Error, ValueError):
            raise NoRootSsh("it did not answer as asked") from None
        if len(data) > MOST:
            raise NoRootSsh("it did not answer as asked")
        texts[name] = data.decode("ascii") if data.isascii() else ""
    custom = texts.get("custom")
    return Read(ca=texts.get("ca") or "", node=(custom if custom is not None else texts.get("node")) or "")


def _why(result: runner.Result) -> str:
    """Why a read over root SSH gave nothing to read, in plain words."""
    if isinstance(result, runner.Done):
        return one_line(f"it ended with status {result.status}: {result.said}", 300)
    if isinstance(result, runner.Cut):
        return one_line(f"it sent more than was asked: {result.why}", 300)
    if isinstance(result, runner.Silent):
        return "it said nothing in time"
    if isinstance(result, runner.TimedOut):
        return "it did not end in time"
    return one_line(result.said or "it could not be reached", 300)


async def read_over_root_ssh(
    job: Job, target: runner.Target, env: Mapping[str, str], files: Mapping[str, str] = HOST_FILES
) -> Read:
    """The host's certificate files, read over root SSH to target (runner.proxmox_host) as a child of job's run, with
    env: each looked at before it is read, none larger than a certificate file is. NoRootSsh where it did not answer,
    or not as asked."""
    result = await runner.run(job, target, read_command(files), None, LIMITS, env)
    if isinstance(result, runner.Done) and result.status == 0:
        return parse(result.lines, files)
    raise NoRootSsh(_why(result))


# ---------------------------------------------------------------- the probes, which carry no credential ----


async def _probed(url: str, pem: str, check_hostname: bool = True) -> str:
    """trust.py's probe of pem at url: "" where the certificate presented verifies against pem alone and the answer is
    HTTP, else why not, in the SSL library's words. It carries no credential. Never given nothing, which trust.py's
    probe would take for its own idea of this machine's store, which a variable of the environment names."""
    import asyncio

    if not pem.strip():
        return "there is no certificate to verify it against"
    return await asyncio.to_thread(_probe, url, pem, TIMEOUT, check_hostname)


async def _shown(url: str) -> dict:
    """trust.py's read of what the API at url presents, with no check and no credential: {"pem", "fingerprint",
    "why"}."""
    import asyncio

    return await asyncio.to_thread(proxmox_trust_shown, url, TIMEOUT)


def _store(tls_ca_file: str | None) -> str:
    """The computer's certificates as one text, certifi's list and then the file tls_ca_file names, their certificates
    alone: what a host with no anchor is checked against. trust.py's pem_certificates stops at a certificate file's
    size, which certifi's list passes, so its blocks are taken here; the client's own context (pin.kept) has said first
    that each file can be used."""
    texts = []
    for name in vendors_trust.files(tls_ca_file):
        try:
            with open(name, "rb") as stream:
                text = stream.read(STORE_MOST).decode("latin-1")
        except OSError:
            raise vendors_trust.Unusable(f"the certificate file {name!r} cannot be read") from None
        texts += [found.group(0) + "\n" for found in STORE_PEM.finditer(text)]
    return "".join(texts)


async def _holds_now(host: Host) -> str:
    """ "" where what checking uses now verifies the API as the client checks it - the host's anchor, its name checked
    unless a fingerprint is recorded, which the certificate presented must then have; or the computer's certificates -
    else why not. vendors_trust.Unusable where the computer's cannot be used, as the client would find."""
    if host.anchor is None:
        pin.kept(None, "", host.tls_ca_file)
        return await _probed(host.url, _store(host.tls_ca_file))
    if not host.fingerprint:
        return await _probed(host.url, host.anchor)
    why = await _probed(host.url, host.anchor, check_hostname=False)
    if why:
        return why
    shown = await _shown(host.url)
    if shown["fingerprint"] != host.fingerprint.upper():
        return f"it presents SHA-256 {shown['fingerprint'] or 'nothing'}, not the fingerprint recorded"
    return ""


# ---------------------------------------------------------------- what is found ----


@dataclasses.dataclass(frozen=True)
class _Found:
    """An anchor worked out: what it is, as a sentence names it, the fingerprint a person compares and --certificate
    gives (the anchor's own first certificate's), whether root SSH vouched for it, and what is said once it is kept."""

    anchor: hosts.Anchor
    what: str
    fingerprint: str
    vouched: bool
    kept: str


def _init(host: Host, **values: object) -> str:
    """init for this host, as the printer writes it."""
    return said("{init}", {"init": Line("init", {"host": host.name, **values})})


def _compare(anchor: hosts.Anchor) -> str:
    """What a person runs on the host to print the fingerprint to compare."""
    return f"{COMPARE} {HOST_FILES['ca' if anchor.kind == 'ca' else 'node']}"


async def _as_node(host: Host, fingerprint: str, pem: str, what: str, vouched: bool, kept: str) -> _Found | Refusal:
    """What the address presents, kept as the node's own certificate held to its fingerprint: the one whose fingerprint
    was shown, read again where pem is "", and verifying as an anchor of its own with no name checked, as the client
    will check it."""
    if not pem:
        shown = await _shown(host.url)
        pem = shown["pem"] if shown["fingerprint"] == fingerprint else ""
    pem = pem_certificates(pem)
    if not pem or pem_fingerprint(pem) != fingerprint:
        return Refusal(
            f"https://{host.endpoint} presented another certificate than SHA-256 {fingerprint} when it was read again:"
            " nothing was kept, and nothing sent with the token"
        )
    why = await _probed(host.url, pem, check_hostname=False)
    if why:
        return Refusal(
            f"what https://{host.endpoint} presents (SHA-256 {fingerprint}) does not verify even as an anchor of its"
            f" own ({one_line(why, 200)}): nothing was kept, and nothing sent with the token"
        )
    return _Found(hosts.Anchor(pem, "node", fingerprint), what, fingerprint, vouched, kept)


async def _found(host: Host, read: Read | None, no_ssh: str) -> _Found | Refusal:
    """What the host is to be held to, where what checking uses now does not verify: from its files read over root
    SSH, where they hold a certificate; else what the address presents, which only a person can vouch for.

    The node's own certificate is the first of its file alone (pem_first): a custom one is served with its chain, and
    an intermediate of that chain made an anchor would pass whatever it issued, for any name, for the host's own."""
    import asyncio

    if read is None:
        own, candidates = "", []
    else:
        own = pem_first(read.node)
        candidates = [{"name": "ca", "pem": read.ca}, {"name": "node", "pem": own}]
    path = host.anchor_said
    if not any(pem_certificates(candidate["pem"]) for candidate in candidates):
        shown = await _shown(host.url)
        if not shown["fingerprint"]:
            return Refusal(
                f"https://{host.endpoint} presented no certificate to read ({one_line(shown['why'], 200)}), and"
                f" {no_ssh}: nothing was sent with the token. Check the address in {host.host_file_said}, and that"
                " the host is up"
            )
        kept = (
            f"what https://{host.endpoint} presents (SHA-256 {shown['fingerprint']}) is kept in {path} as you vouched"
            " for it, held to its fingerprint: every run checks it"
        )
        what = "the certificate it presents"
        return await _as_node(host, shown["fingerprint"], shown["pem"], what, False, kept)
    picked = await asyncio.to_thread(proxmox_trust_pick, candidates, host.url, TIMEOUT)
    if picked["name"] in hosts.TRUST_KINDS:
        kind, fingerprint = picked["name"], picked["fingerprint"]
        anchor = hosts.Anchor(picked["pem"], kind, fingerprint if kind == "node" else "")
        held = ", held to its fingerprint" if kind == "node" else ""
        kept = (
            f"Proxmox's certificate verifies against {KINDS[kind]} (SHA-256 {fingerprint}), read over root SSH and"
            f" kept in {path}{held}: every run checks it"
        )
        return _Found(anchor, f"{KINDS[kind]}, read over root SSH,", fingerprint, True, kept)
    # The host's own under another name is the node's own certificate, byte for byte, or one the host's CA signed, and
    # nothing else. So the probe that checks no name is handed the CA alone, and the node's certificate is compared,
    # never made an anchor: one that can sign would pass what it signed for the host's own.
    presented = await asyncio.to_thread(proxmox_trust_presented, candidates[:1], host.url, TIMEOUT)
    if not presented["fingerprint"]:
        return Refusal(
            f"https://{host.endpoint} presented no certificate to read ({one_line(presented['why'], 200)}), so nothing"
            f" shows that the Proxmox host answers there, and nothing was sent with the token. Check the address in"
            f" {host.host_file_said}, and that the host is up",
            token_held=True,
        )
    if not (presented["host"] or (own != "" and presented["fingerprint"] == pem_fingerprint(own))):
        return Refusal(
            f"https://{host.endpoint} presents a certificate (SHA-256 {presented['fingerprint']}) that is none of the"
            f" host's, as root SSH to {host.address} reads them, so something other than the Proxmox host answers"
            f" there, and nothing was sent with the token. Check the address in {host.host_file_said}, and what"
            " answers at it",
            token_held=True,
        )
    fingerprint = presented["fingerprint"]
    kept = (
        f"what https://{host.endpoint} presents is the host's own (SHA-256 {fingerprint}), as root SSH reads it, and"
        f" does not name {host.address}: kept in {path} as the node's own certificate, held to its fingerprint and not"
        " to a name: every run checks it"
    )
    what = "the certificate it presents, the host's own as root SSH reads it under another name,"
    return await _as_node(host, fingerprint, "", what, True, kept)


def _kept(host: Host, found: _Found, said_text: str) -> Kept | Refusal:
    """found kept for host, held first to what the client takes (pin.kept, the one way a context is made here);
    changed where it is not what the host keeps now."""
    anchor = found.anchor
    try:
        pin.kept(anchor.pem, anchor.fingerprint, host.tls_ca_file)
    except ValueError:
        return Refusal(f"{found.what} holds no certificate the client can use: nothing was sent with the token")
    same = (
        host.anchor is not None
        and pem_certificates(host.anchor) == anchor.pem
        and (host.trust_kind, host.fingerprint.upper()) == (anchor.kind, anchor.fingerprint)
    )
    return Kept(anchor=anchor, changed=not same, said=said_text)


async def _verified(host: Host, given: str) -> Kept | Refusal:
    """What host keeps where what checking uses now verifies it: nothing changes. A fingerprint given is compared first:
    with the kept anchor's own (the one recorded beside a node's certificate, else its first certificate's, a CA's own),
    or, where the computer's certificates verify, with what the address presents, a match keeping no anchor still;
    either way a match is said, and a mismatch is a refusal that names both."""
    if host.anchor is None:
        verifies = (
            "Proxmox's certificate verifies against this computer's certificates (certifi's list, with the file"
            " tls_ca_file names)"
        )
        if not given:
            return Kept(None, False, f"{verifies}: the host keeps no anchor, and every run checks it")
        fingerprint = (await _shown(host.url))["fingerprint"]
        what = f"what https://{host.endpoint} presents"
        if given != fingerprint:
            return Refusal(
                f"--certificate {given} is not {what} (SHA-256 {fingerprint or 'none: nothing was read again'}), which"
                " this computer's certificates verify: nothing was kept, and nothing sent with the token"
            )
        return Kept(
            None,
            False,
            f"{verifies}, and --certificate matched {what} (SHA-256 {fingerprint}): the host keeps no anchor, and every"
            " run checks it",
        )
    anchor = hosts.Anchor(pem_certificates(host.anchor), host.trust_kind or "node", host.fingerprint.upper())
    verifies = f"Proxmox's certificate verifies against {host.anchor_said}"
    if not given:
        return Kept(anchor, False, f"{verifies}: every run checks it")
    fingerprint = anchor.fingerprint if anchor.kind == "node" and anchor.fingerprint else pem_fingerprint(anchor.pem)
    if given != fingerprint:
        return Refusal(
            f"--certificate {given} is not {KINDS[anchor.kind]}, the anchor kept in {host.anchor_said} (SHA-256"
            f" {fingerprint}), against which Proxmox's certificate verifies: nothing was changed, and nothing sent with"
            " the token"
        )
    return Kept(
        anchor,
        False,
        f"{verifies}, and --certificate matched {KINDS[anchor.kind]} kept there (SHA-256 {fingerprint}): every run"
        " checks it",
    )


# ---------------------------------------------------------------- the step ----


async def work_out(host: Host, given: str, root_ssh: RootSsh | None, ask: Ask | None) -> Kept | Refusal:
    """What host's API's certificate is held to from now on, worked out with no credential sent: Kept, the anchor to
    keep or none, or a Refusal. given is --certificate's fingerprint, "" for none; root_ssh reads the host's certificate
    files (read_over_root_ssh, bound to the run's job and the host), None where root SSH is not to be tried; ask puts a
    Question to the person and gives True for their yes, anything else being a no, None where nobody is there
    (--no-prompt). See the module's docstring."""
    given = given.strip().upper()
    try:
        why_not = await _holds_now(host)
    except vendors_trust.Unusable as error:
        return Refusal(str(error))
    if not why_not:
        return await _verified(host, given)

    read: Read | None = None
    no_ssh = "root SSH was not tried"
    if root_ssh is not None:
        try:
            read = await root_ssh()
        except NoRootSsh as error:
            no_ssh = f"root SSH to {host.address} did not answer: {one_line(str(error), 200)}"
        else:
            no_ssh = f"root SSH to {host.address} read no certificate of the host's"
    found = await _found(host, read, no_ssh)
    if isinstance(found, Refusal):
        return found
    if given and given != found.fingerprint:
        return Refusal(
            f"--certificate {given} is not {found.what.rstrip(',')} (SHA-256 {found.fingerprint}): nothing was kept,"
            " and nothing sent with the token"
        )
    old = None if host.anchor is None else pem_fingerprint(host.anchor)
    replacing = old is not None and old != pem_fingerprint(found.anchor.pem)
    kept = found.kept
    if replacing:
        kept = (
            f"Proxmox's certificate no longer verified against {host.anchor_said} (SHA-256 {old}), and"
            f" {found.what.rstrip(',')} (SHA-256 {found.fingerprint}) was kept in its place: every run checks it"
        )
    if given or (found.vouched and not replacing):
        return _kept(host, found, kept)

    # Only a person can vouch for it: shown, and asked.
    compare = _compare(found.anchor)
    if replacing:
        lines = (
            f"Proxmox's API at {host.endpoint} no longer verifies against {host.anchor_said}, whose certificate is",
            f"  SHA-256 {old}",
            f"({one_line(why_not, 200)}). {found.what[:1].upper()}{found.what[1:]} is",
            f"  SHA-256 {found.fingerprint}",
            "Proxmox renews the node's certificate within two years, and a reinstalled host has a new CA; a"
            " certificate that changed is also what someone answering in its place would present. Compare it with"
            f" what the host prints: {compare}.",
        )
        prompt = "Trust it in its place? y or n"
        unasked = (
            f"Proxmox's certificate no longer verifies against {host.anchor_said} (SHA-256 {old}), and"
            f" {found.what} is SHA-256 {found.fingerprint}: it is put in its place only on your word, so"
            f" nothing was sent with the token. {_init(host)} at a terminal asks whether to trust it in its place, and"
            f" {_init(host, certificate=found.fingerprint)} puts it there once you have compared it with what the host"
            f" prints ({compare})"
        )
    else:
        lines = (
            f"Proxmox's API at {host.endpoint} presents a certificate that nothing on the host vouches for ({no_ssh}):",
            f"  SHA-256 {found.fingerprint}",
            f"Compare it with what the host prints: {compare} (or pveproxy-ssl.pem, where the host serves one of its"
            " own). Kept, it is held to that fingerprint, and a certificate the host is given anew is asked about"
            " again.",
        )
        prompt = "Trust it? y or n"
        unasked = (
            f"https://{host.endpoint} presents a certificate (SHA-256 {found.fingerprint}) that nothing on the host"
            f" vouches for ({no_ssh}): only you can, so nothing was kept and nothing sent with the token. Compare it"
            f" with what the host prints ({compare}), then {_init(host, certificate=found.fingerprint)} keeps it"
        )
    if ask is None:
        return Refusal(unasked)
    # A yes is True and nothing else: an answer handed back as the choice made ("n") or a number is a no.
    if await ask(Question(lines, prompt)) is not True:
        return Refusal(
            f"SHA-256 {found.fingerprint} was not trusted: nothing was kept, and nothing sent with the token"
        )
    return _kept(host, found, kept)
