"""A host of the fleet, opened from the fleet's files: its client, its pool and what its runs are held to
(docs/architecture/engine.md §16.9).

`connect(home, reached)` opens the host a run is against (hosts.py's `reach` chose it): `fleet.json`'s record, the vault
opened with its key as vaultkey.py finds it, the host's token's secret from it, and one client (pve/client.py) pinned as
the host keeps its anchor (pve/pin.py's `kept`), through the proxy the setting `network_proxy` gives. It sends nothing;
a `Host` closes its client. `client_for` makes another host's client the same way, for a run that asks the fleet's other
hosts. Where the host's files say no client that can be made - its address, its anchor, the file of certificates
`tls_ca_file` names (vendors/trust.py's `Unusable`), its proxy - it is `Refused`, in one sentence, never a traceback.

A `Host` keeps what the route to its machines is settled from (engine/routes.py): the fleet's home, host.yml as it was
read and the run's values, read-only. It keeps the environment its settings were read from, the fleet's hosts read from
it, and the run's values out of its repr: a proxy that needs a password is given in HTTPS_PROXY, and every secret a
person exported is in it.

Here, and not in engine/, so that status, which may not import the engine (card G15's boundary), opens a host as every
workflow does (card G16), and the two never part. It imports fleet/, pve/ for the client, vendors/ for the proxy's
check and the certificates' refusal, schema/, and of today's lib/claude_vm/ only hosts/ and text.py; nothing of
engine/, no pydantic, ansible or MCP SDK (tests/unit/test_aw_ownership.py).
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from types import MappingProxyType

from ..hosts import model
from ..pve import pin
from ..pve.client import Proxmox, Token
from ..schema.printer import said
from ..schema.words import Line
from ..secret import Secret
from ..text import one_line
from ..vendors import proxy
from ..vendors.trust import Unusable
from . import hosts, overrides, paths, sealed, secrets, vaultkey
from . import record as fleet_record

__all__ = ["LOCALHOST", "Host", "Refused", "client_for", "connect", "sentence"]

# The name every computer reaches itself by: no VM takes it, as no host of the fleet's.
LOCALHOST = "localhost"


class Refused(Exception):
    """One sentence that says why a run reads or changes nothing here: never a secret, with no "error: " before it."""

    def __init__(self, sentence: str) -> None:
        self.sentence = sentence
        super().__init__(sentence)


@dataclasses.dataclass(frozen=True, eq=False)
class Host:
    """The host a run is against, opened: its name, its client, its pool, the network card a VM's address is read on
    (proxmox_nic), the account a run logs in as (vm_user), the default VM's name (vm_default_name), whether the aliases
    are on (claude_ssh_config), fleet.json's record, the fleet's hosts (layout), the environment its settings were read
    from, the names a VM may not take (localhost and the fleet's hosts) and those this computer reaches a host by (with
    each host's address), and every host's token's secret the vault holds, by the host's name, which asking another host
    needs. Beside them, what the route to its machines is settled from (engine/routes.py): the fleet's home, its
    host.yml as it was read (fields: its address, ssh_user, ssh_port and jump), and every setting's value for the run
    against it (values, overrides.resolve's, read-only), vm_ssh_route, ssh_identity_file, ssh_executable and
    vm_ssh_options among them. The environment, the layout that holds it, the run's values (a path among them is
    expanded from HOME, and vm_ssh_options is what a person wrote) and the secrets are kept out of its repr. A context
    manager that closes the client."""

    name: str
    client: Proxmox
    pool: str
    nic: str
    user: str
    default_name: str
    aliases: bool
    fleet: fleet_record.FleetRecord
    layout: hosts.Layout = dataclasses.field(repr=False)
    environ: Mapping[str, str] = dataclasses.field(repr=False)
    hosts_named: tuple[str, ...]
    reached_by: tuple[str, ...]
    home: paths.Home
    fields: hosts.HostFile
    values: Mapping[str, overrides.Value] = dataclasses.field(repr=False)
    tokens: Mapping[str, Secret] = dataclasses.field(repr=False)
    # The VMs' console password (the vault's vm_password), None where the vault holds none: sent as cloud-init's
    # cipassword when a VM is created (card G3), never said.
    password: Secret | None = dataclasses.field(default=None, repr=False)

    def __enter__(self) -> Host:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    @property
    def host_flag(self) -> str:
        """The host's flag a line that names a command ends with: the host's name with more than one, "" with one."""
        return self.name if self.layout.many() else ""


def sentence(error: BaseException | str) -> str:
    """A refusal of fleet/'s, or another error, as one sentence of a Refused: one line, no "error: " before it."""
    return one_line(str(error), None).removeprefix("error: ")


def _tokens(opened: Mapping[str, Secret]) -> dict[str, Secret]:
    """Of the vault's map, each host's token's secret, by the host's name."""
    found = {}
    for entry, value in opened.items():
        try:
            row, owner = secrets.parsed(entry)
        except secrets.Refused:
            continue
        if row.name == "token" and owner is not None:
            found[owner] = value
    return found


def client_for(
    name: str,
    fields: hosts.HostFile,
    anchor: str | None,
    values: Mapping[str, overrides.Value],
    tokens: Mapping[str, Secret],
    environ: Mapping[str, str],
) -> Proxmox:
    """The client of the host called name, as its folder keeps it: its address and port, its anchor (with its
    fingerprint beside it, where it keeps one) or the computer's certificates, its token from the vault, and the proxy
    the setting network_proxy gives for its address. It sends nothing. Refused where the vault holds no token for it,
    or its files say nothing a client can be made of: an address, an anchor or a fingerprint that is none, a file of
    certificates (tls_ca_file) that is not there, cannot be read or holds none, a proxy that is none."""
    secret = tokens.get(name)
    if secret is None:
        raise Refused(
            said(
                "the vault holds no token's secret for {name}, so nothing of it can be read: {vault} puts it",
                {"name": name, "vault": Line("vault", {"name": "token", "host": name})},
            )
        )
    # The address and port as a URL has them: an IPv6 address in brackets.
    at = f"[{fields.address}]" if ":" in fields.address else fields.address
    url = f"https://{at}:{fields.api_port}"
    try:
        pinned = pin.kept(anchor, fields.fingerprint if anchor is not None else "", values["tls_ca_file"].value or None)
        through = proxy.choose(values["network_proxy"].value, environ, url)
        token = Token(f"{fields.token_user}!{fields.token_id}", secret)
    except (ValueError, OSError, Unusable) as error:
        raise Refused(f"{name}'s API cannot be reached as its files say: {sentence(error)}") from None
    return Proxmox(url, pinned, token, through)


def _named_hosts(layout: hosts.Layout) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The names no VM may take (localhost, and each host of the fleet's), and every name this computer reaches one of
    them by: those, and each host's address."""
    named = [LOCALHOST, *(name for name in layout.folders() if not model.name_refusal(name))]
    addresses = [one.address for one in layout.every() if one.address]
    return tuple(dict.fromkeys(named)), tuple(dict.fromkeys([*named, *addresses]))


def connect(
    home: paths.Home,
    reached: hosts.Reached,
    values: Mapping[str, overrides.Value] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    vault: Mapping[str, Secret] | None = None,
) -> Host:
    """The host reached is, opened: see the module's docstring. values is every setting's value for the run against it
    (overrides.resolve's), read here where it is not given. vault is the vault's map where the caller opened it already
    (a run that held its --set values to the vault's secrets first), so that its key is asked for once; read here where
    it is not given. Refused, in one sentence, where the fleet has no record, the vault cannot be opened or holds no
    token for the host, the host names no pool, or its files say no API that can be reached. Sends nothing."""
    env = os.environ if environ is None else environ
    name = reached.host.name
    layout = hosts.Layout(home, env)
    try:
        if values is None:
            values = overrides.resolve(home, name, environ=env)
        fleet = fleet_record.read(home)
        if fleet is None:
            raise Refused(
                said("the fleet has no record of its own (fleet.json) yet: {init} writes it", {"init": Line("init")})
            )
        if vault is None:
            key = vaultkey.open(
                home,
                key_file=values["vault_key_file"].value,
                key_command=values["vault_key_command"].value,
                environ=env,
            )
            vault = sealed.read(home, key) or {}
        tokens = _tokens(vault)
        password = vault.get(secrets.entry("vm_password"))
        named, reached_by = _named_hosts(layout)
    except (sealed.Refused, fleet_record.Refused, overrides.Refused, paths.Refused, hosts.Refused) as error:
        raise Refused(sentence(error)) from None
    pool = values["proxmox_pool"].value
    if not pool:
        raise Refused(
            said(
                "{name} names no pool (proxmox_pool), and the fleet's VMs are its members: {init} writes it",
                {"name": name, "init": Line("init", {"host": name})},
            )
        )
    return Host(
        name=name,
        client=client_for(name, reached.fields, reached.anchor, values, tokens, env),
        pool=pool,
        nic=values["proxmox_nic"].value,
        user=values["vm_user"].value,
        default_name=values["vm_default_name"].value,
        aliases=values["claude_ssh_config"].value is not False,
        fleet=fleet,
        layout=layout,
        environ=env,
        hosts_named=named,
        reached_by=reached_by,
        home=home,
        fields=reached.fields,
        values=MappingProxyType(dict(values)),
        tokens=MappingProxyType(tokens),
        password=password if isinstance(password, Secret) else None,
    )
