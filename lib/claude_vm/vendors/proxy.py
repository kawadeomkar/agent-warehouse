"""The proxy a request of the new engine goes through: the one the environment names, by `requests`' rule
(docs/architecture/engine.md §16.7).

`choose` takes `requests`' rule (2.34's `get_environ_proxies` and `select_proxy`), the one a person behind a proxy
expects of a Python program: the `https` entry of the environment's proxies, else its `all` entry (`HTTPS_PROXY`,
`ALL_PROXY` and their lower-case forms, the lower-case one first); none where `no_proxy` or `NO_PROXY` names the host,
by a name, a suffix, `*`, or, for a host that is an IPv4 address, the address or a CIDR range holding it. Where the
environment names no proxy at all, macOS's network settings are read as the standard library reads them, their
exceptions included; Linux has no such settings. That is the setting `network_proxy` at `environment`, its default.
At `none` there is no proxy whatever the environment says, the stricter form and an opt-in; at an `http://` URL, that
proxy. A URL the setting names names no user and no password, which a file of settings, a run's record and what
`settings` prints would hold in the clear: a proxy that needs them is given as `HTTPS_PROXY` in the environment, which
`environment` reads and the transport holds them from as a `Secret`. `setting_refusal` is that one check of the setting,
which `choose` makes and the overrides' `typed` (fleet/overrides.py) makes of every value a file or `--set` gives it.

`choose` reads only the `environ` it is given, never this process's, so of `urllib.request` it calls only the readers
that take what they read as an argument or read the system's settings: `proxy_bypass_environment`, given the proxies
read from `environ`, and macOS's `getproxies_macosx_sysconf` and `proxy_bypass_macosx_sysconf`. Never
`urllib.request`'s `getproxies` or `proxy_bypass` themselves, which read `os.environ`, and never an opener: the one
transport (`transport.py`) sends every request, and takes the proxy chosen here as an argument.
"""

from __future__ import annotations

import socket
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping

# The setting network_proxy's two words; any other value is an http:// URL.
ENVIRONMENT = "environment"
NONE = "none"
# What network_proxy's value is refused for, each said after the setting's name and never with the value: a URL that
# names a user, with or without a password; and one that is neither word nor an http:// URL with a host.
CREDENTIALS = (
    "takes no user or password: give a proxy that needs them as HTTPS_PROXY in the environment, which network_proxy:"
    " environment reads"
)
NOT_ONE = f"is {ENVIRONMENT}, {NONE} or an http:// URL with a host"


def setting_refusal(setting: str) -> str | None:
    """What the setting ``network_proxy`` is refused for, said after its name, or None where it is one: ``environment``,
    ``none``, or an ``http://`` URL with a host, a port where it names one, and no user or password, a %-escaped ``@``
    among them. The one check of the setting: ``choose`` makes it, and so does the overrides' ``typed`` of every value a
    file or ``--set`` gives it. It never says the value."""
    if setting in (ENVIRONMENT, NONE):
        return None
    try:
        parts = urllib.parse.urlsplit(setting)
        port = parts.port
    except ValueError:
        return NOT_ONE
    if "@" in urllib.parse.unquote(parts.netloc):
        return CREDENTIALS
    if parts.scheme != "http" or not parts.hostname or port == 0:
        return NOT_ONE
    return None


def choose(setting: str, environ: Mapping[str, str], url: str) -> str | None:
    """The proxy a request to ``url`` goes through, as an ``http://`` URL (or the scheme the environment gave, which
    the transport refuses by name), or None to go straight to it: by the setting ``network_proxy``, reading only
    ``environ`` (the module's docstring has the rule). Raises ValueError, in ``setting_refusal``'s words, for a setting
    that is neither word nor an ``http://`` URL with a host and no user or password."""
    refused = setting_refusal(setting)
    if refused is not None:
        raise ValueError(f"network_proxy {refused}")
    if setting == NONE:
        return None
    if setting != ENVIRONMENT:
        return setting
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if host is None or _named_in_no_proxy(host, parts.port, environ):
        return None
    found = _environment(environ)
    try:
        bypass = urllib.request.proxy_bypass_environment(host, found) if found else _system_bypass(host)
    except (TypeError, OSError):
        bypass = False  # requests' own guard around the same reading: one that fails bypasses nothing
    if bypass:
        return None
    if not found:
        found = _system_proxies()
    proxy = found.get("https", found.get("all"))
    if not proxy:
        return None
    # A value with no scheme is an http:// proxy, as requests reads one.
    return proxy if "://" in proxy else f"http://{proxy}"


def _environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The proxies ``environ`` names, as the standard library's getproxies_environment reads os.environ: each
    <scheme>_proxy variable by its scheme in lower case, an empty one removing what its upper-case form gave, the
    lower-case form first. (Its CGI rule touches only the http entry, which choose never reads.)"""
    proxies: dict[str, str] = {}
    seen = []
    for name, value in environ.items():
        if len(name) > 5 and name[-6] == "_" and name[-5:].lower() == "proxy":
            scheme = name[:-6].lower()
            seen.append((name, value, scheme))
            if value:
                proxies[scheme] = value
    for name, value, scheme in seen:
        if name[-6:] == "_proxy":
            if value:
                proxies[scheme] = value
            else:
                proxies.pop(scheme, None)
    return proxies


def _named_in_no_proxy(host: str, port: int | None, environ: Mapping[str, str]) -> bool:
    """Whether no_proxy (or NO_PROXY) names host, as requests' should_bypass_proxies reads it: for an IPv4 address,
    the address or a CIDR range that holds it; for a name, the name or a suffix of it at a dot, with or without the
    port."""
    no_proxy = environ.get("no_proxy") or environ.get("NO_PROXY")
    if not no_proxy:
        return False
    entries = [entry for entry in no_proxy.replace(" ", "").split(",") if entry]
    if _ipv4(host):
        return any(_in_network(host, entry) if _cidr(entry) else host == entry for entry in entries)
    with_port = f"{host}:{port}" if port else host
    for entry in entries:
        entry = entry.lstrip(".")
        if entry in (host, with_port) or host.endswith(f".{entry}") or with_port.endswith(f".{entry}"):
            return True
    return False


def _ipv4(text: str) -> bool:
    """Whether text is an IPv4 address in any form inet_aton takes, as requests tells one."""
    try:
        socket.inet_aton(text)
    except OSError:
        return False
    return True


def _cidr(text: str) -> bool:
    """Whether text is an IPv4 range as requests takes one: an address, one /, a length from 1 to 32."""
    if text.count("/") != 1:
        return False
    address, bits = text.split("/")
    try:
        length = int(bits)
    except ValueError:
        return False
    return 1 <= length <= 32 and _ipv4(address)


def _in_network(address: str, network: str) -> bool:
    """Whether the IPv4 address is in the range, both as inet_aton reads them."""
    base, bits = network.split("/")
    mask = (0xFFFFFFFF << (32 - int(bits))) & 0xFFFFFFFF
    number = int.from_bytes(socket.inet_aton(address), "big")
    return (number & mask) == (int.from_bytes(socket.inet_aton(base), "big") & mask)


def _system_proxies() -> dict[str, str]:
    """The proxies of the system's own settings, where the environment names none: macOS's network settings, as the
    standard library reads them; none on Linux, whose proxy settings are the environment's."""
    if sys.platform != "darwin":
        return {}
    return dict(urllib.request.getproxies_macosx_sysconf())


def _system_bypass(host: str) -> bool:
    """Whether the system's own settings go straight to host: macOS's exceptions and its simple host names, as the
    standard library reads them; never on Linux."""
    if sys.platform != "darwin":
        return False
    return bool(urllib.request.proxy_bypass_macosx_sysconf(host))
