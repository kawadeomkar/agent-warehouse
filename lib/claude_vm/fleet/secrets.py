"""The vault's closed list of named secrets (docs/architecture/engine.md §16.9).

`ROWS` is the list, as literals: each `Row(name, scope, kind, description)` names one secret the vault may keep, the
scope it is kept at, the kind its value must be (`KINDS`) and what a person reads of it. The list is closed: a name
outside it is never kept, and a secret under a name of the person's own has no place in it. A secret of scope `global`
is one for the fleet, kept under its name; one of scope `host` is each host's own, kept as `<name>@<host>`; and one of
scope `machine` is each adopted machine's own, kept as `<name>@<machine>`. That key, the secret's entry, is what the
vault's map is keyed by (fleet/sealed.py): `entry(name, host=..., machine=...)` makes it and `parsed(entry)` reads one
back, each refusing a name outside the list, an owner the row's scope does not take or lacks, and an owner that is no
name of a host's or a machine's. `held(row, value)` says whether a value is of its row's kind.

The row is not called `Secret`: `claude_vm.secret.Secret` is the value's wrapper, which the vault hands back.

Every refusal is a `Refused`, one sentence that names a name only where it has the shape of one (`secret_name`'s
pattern, a host's or a machine's), and never a value: a secret pasted where a name goes would be on the screen.

Standard library only, with the patterns and the settings table's `ValueRule`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from claude_vm.fleet.registry import SCOPES, ValueRule
from claude_vm.schema import patterns

# What a secret's value must be, by its row's kind. A value is never empty: a secret not set is not in the vault.
KINDS: Mapping[str, ValueRule] = MappingProxyType(
    {
        # A Proxmox token's secret, as pveum prints it (today's init holds it the same way: lib/claude_vm/init.py's
        # _UUID, held equal by tests/unit/test_aw_fleet_sealed.py).
        "uuid": ValueRule(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "a UUID in lower case, as Proxmox prints a token's secret, like 01234567-89ab-cdef-0123-456789abcdef",
        ),
        # A password a person chose: one line, which a console and sudo each read whole.
        "password": ValueRule(
            r"[^\x00-\x1f\x7f-\x9f  \ud800-\udfff]{1,1024}",
            "one line of text with no control character, at most 1024 characters",
        ),
        # A vendor's token or key: one word, as each vendor prints one.
        "word": ValueRule(r"[!-~]{1,4096}", "one word of printable ASCII with no space, at most 4096 characters"),
        # Tailscale's auth key, as today's init holds one (lib/claude_vm/init.py's _AUTH_KEY, held equal by the same
        # test): never an API access token, which the same Keys page makes and which joins nothing.
        "auth_key": ValueRule(
            r"(?!tskey-api-)[A-Za-z0-9._:/?=&+-]{20,512}",
            "a key as Tailscale printed it (tskey-auth-..., or an OAuth client secret, tskey-client-...) or your"
            " control server did, never an API access token (tskey-api-...): one word of 20 to 512 letters, digits"
            " and . _ : / ? = & + -",
        ),
    }
)
# An adopted machine's name in an entry, after its secret's name and an @: as the overrides take one after a host's
# (fleet/overrides.py's MACHINE, held equal by the same test).
MACHINE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
# What joins a secret's name to its owner's in an entry: no name of a secret's, a host's or a machine's holds it.
AT = "@"
# The longest name a refusal says back, where it has the shape of a secret's name.
SAID_MOST = 64


class Refused(ValueError):
    """A name or an owner the vault does not keep: the sentence says why, and never a value."""


@dataclass(frozen=True)
class Row:
    """One secret of the closed list: its name, its scope (`global`, `host` or `machine`), its kind (a key of KINDS)
    and its description. Each field is held as the row is made: a ValueError says which."""

    name: str
    scope: str
    kind: str
    description: str

    def __post_init__(self) -> None:
        if not patterns.full("secret_name", self.name):
            raise ValueError(f"a secret's name is {patterns.PATTERNS['secret_name'].words}")
        if self.scope not in SCOPES:
            raise ValueError(f"{self.name}'s scope is one of {', '.join(SCOPES)}")
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}'s kind is one of {', '.join(KINDS)}")
        if not self.description.strip():
            raise ValueError(f"{self.name} has a description")


# The closed list, in the order a person reads it.
ROWS: tuple[Row, ...] = (
    Row(
        "token",
        "host",
        "uuid",
        "The secret of the engine's one API token on this host, as Proxmox printed it when the token was made.",
    ),
    Row(
        "vm_password",
        "global",
        "password",
        "A password for the VMs' user: the Proxmox console's, sent when a VM is created, and the one sudo asks for "
        "where it asks. SSH stays key-only either way. Every VM gets the same one, and each VM can read its hash: use "
        "it nowhere else.",
    ),
    Row(
        "become_password",
        "machine",
        "password",
        "The password sudo asks for on this adopted machine, given to sudo there alone where it asks.",
    ),
    Row(
        "anthropic_api_key",
        "global",
        "word",
        "An Anthropic API key, so Claude Code on the VMs is billed to API credits with no sign-in. It outranks a "
        "Claude account's sign-in, and Remote Control needs that sign-in: leave it unset to have Remote Control.",
    ),
    Row(
        "github_token",
        "global",
        "word",
        "A GitHub token: private repositories are listed and cloned, and gh on each VM is signed in with it. A classic "
        "token needs repo and read:org; a fine-grained one Metadata (read) and Contents and Pull requests (read and "
        "write) on all repositories. Unset, each VM signs in once by GitHub's device flow.",
    ),
    Row(
        "tailscale_auth_key",
        "global",
        "auth_key",
        "A Tailscale auth key, so each new VM joins your tailnet with nothing to click: reusable or one-off, "
        "pre-approved and tagged, never ephemeral. Used only for a VM's first join. Unset, each VM signs in by "
        "Tailscale's web login.",
    ),
    Row(
        "claude_setup_token",
        "global",
        "word",
        "A long-lived token of your Claude account, as claude setup-token prints it, which signs Claude Code in on "
        "each VM with nothing to click.",
    ),
    Row(
        "tailscale_api_token",
        "global",
        "word",
        "A Tailscale API access token (tskey-api-..., at most 90 days), which deletes a VM's device from your tailnet "
        "when the VM is destroyed. Used only on this computer, never sent to a VM.",
    ),
    Row(
        "tailscale_oauth_client_id",
        "global",
        "word",
        "The ID of a Tailscale OAuth client with the devices:core scope, which with its secret deletes a VM's device "
        "from your tailnet when the VM is destroyed, and does not expire. Used only on this computer.",
    ),
    Row(
        "tailscale_oauth_client_secret",
        "global",
        "word",
        "That OAuth client's secret (tskey-client-...). Used only on this computer, never sent to a VM.",
    ),
)
_BY_NAME: Mapping[str, Row] = MappingProxyType({each.name: each for each in ROWS})


def names() -> tuple[str, ...]:
    """The closed list's names, in its order."""
    return tuple(_BY_NAME)


def row(name: str) -> Row | None:
    """The row of name, or None where the list keeps no secret of that name."""
    return _BY_NAME.get(name) if isinstance(name, str) else None


def held(each: Row, value: str) -> bool:
    """Whether value, whole, is of each's kind."""
    return isinstance(value, str) and KINDS[each.kind].allows(value)


def _row(name: object) -> Row:
    """name's row; Refused where the list has none, said by name only where it has a name's shape."""
    if not isinstance(name, str):
        raise Refused("error: a secret's name is a string")
    found = row(name)
    if found is None:
        if patterns.full("secret_name", name) and len(name) <= SAID_MOST:
            raise Refused(f"error: {name} is no secret the vault keeps: it keeps {', '.join(names())}")
        raise Refused(
            f"error: that is no secret's name: a name the vault keeps is {patterns.PATTERNS['secret_name'].words}"
        )
    return found


def _owner(each: Row, host: str | None, machine: str | None) -> str | None:
    """The owner's name an entry of each joins its name to: the host's, the machine's, or None for the fleet's."""
    if each.scope == "global":
        if host is not None or machine is not None:
            raise Refused(f"error: {each.name} is one for the whole fleet, never a host's or a machine's own")
        return None
    if each.scope == "host":
        if machine is not None:
            raise Refused(f"error: {each.name} is each host's own, never a machine's: name the host it is for")
        if host is None:
            raise Refused(f"error: {each.name} is each host's own: name the host it is for")
        if not isinstance(host, str) or not patterns.full("current_host", host):
            raise Refused(f"error: the host {each.name} is for is not {patterns.PATTERNS['current_host'].words}")
        return host
    if host is not None:
        raise Refused(f"error: {each.name} is each adopted machine's own, never a host's: name the machine it is for")
    if machine is None:
        raise Refused(f"error: {each.name} is each adopted machine's own: name the machine it is for")
    if not isinstance(machine, str) or re.fullmatch(MACHINE, machine) is None:
        raise Refused(
            f"error: the machine {each.name} is for is no machine's name: letters, digits, dots, underscores and"
            " hyphens, starting with a letter or a digit"
        )
    return machine


def entry(name: str, *, host: str | None = None, machine: str | None = None) -> str:
    """The key the vault keeps a secret under: its name for the fleet's, `<name>@<host>` for a host's own and
    `<name>@<machine>` for an adopted machine's. Refused where the list has no such name, or where the owner its scope
    asks for is missing, of another scope, or no name of a host's or a machine's."""
    each = _row(name)
    owner = _owner(each, host, machine)
    return each.name if owner is None else f"{each.name}{AT}{owner}"


def parsed(key: object) -> tuple[Row, str | None]:
    """An entry read back: its row and its owner (a host's or a machine's name, None for the fleet's), with the same
    refusals as entry's."""
    if not isinstance(key, str):
        raise Refused("error: a secret's entry is a string")
    name, at, owner = key.partition(AT)
    each = _row(name)
    if not at:
        return each, _owner(each, None, None)
    if each.scope == "host":
        return each, _owner(each, owner, None)
    if each.scope == "machine":
        return each, _owner(each, None, owner)
    raise Refused(f"error: {each.name} is one for the whole fleet, never a host's or a machine's own")
