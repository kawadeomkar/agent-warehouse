"""The computer's certificates: certifi's list, with the file the setting tls_ca_file names added to it, and the TLS
context made from them alone (docs/architecture/engine.md §16.7).

GitHub, Tailscale and the download servers are verified against them, and so is the Proxmox API wherever its host keeps
no anchor file of its own (pve/pin.py). certifi's bundle is named by the path certifi gives, the same on every system;
the setting tls_ca_file names a file of PEM certificates that is added to it, never put in its place: a company's root,
say. That is a recorded change from 2.x, whose vendor calls were Ansible's uri, verified against the system's own store
of the machine that made them, which the interpreter's certificate variables change. Neither that store nor any
certificate variable is read here: tls_ca_file is the one way to add a root, and the refusal of a certificate whose
issuer is unknown (the transport's Untrusted) says so. There is no table of each system's paths.

context() loads those files and nothing else: not the interpreter's own store, nothing the environment names, not
macOS's keychain. It makes the context by hand, since the standard library's ready-made one loads the interpreter's
store where it is given no file, and reads the environment for a file to log the session's keys to. The proxy is not
this module's: proxy.choose gives it to the transport beside this context.
"""

from __future__ import annotations

import os
import ssl

# The computer's setting that names a file of certificates added to certifi's: every refusal of a file names it.
SETTING = "tls_ca_file"
# What a context verifies with beside the files: the flags Python 3.13's own context sets, and today's requests on it,
# set here on every Python, so that one before 3.13 is no looser.
FLAGS = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
# What mends a missing or broken certifi: the words the command line says for any package its environment lacks.
INSTALL = "run aw install"


class Unusable(Exception):
    """A file of the computer's certificates cannot be used: it is not there, cannot be read, or holds no certificate;
    or certifi is not installed. Its sentence says which file, and what mends it: the setting tls_ca_file for the file
    it names, aw install for certifi's."""


def _certifi() -> str:
    """certifi's bundle, by the path certifi gives."""
    try:
        import certifi
    except ImportError:
        raise Unusable(
            f"certifi, whose list of certificates every connection with no anchor of its own trusts, is not installed"
            f" in this Python environment: {INSTALL}"
        ) from None
    return certifi.where()


def files(setting: str | os.PathLike[str] | None = None) -> tuple[str, ...]:
    """The files a context trusts, in the order it loads them: certifi's bundle, and then the one the setting
    tls_ca_file names, where it is given (neither None nor empty). Whether each can be used is context()'s to say."""
    named = os.fspath(setting) if setting is not None else ""
    return (_certifi(), named) if named else (_certifi(),)


def _held(name: str) -> int:
    """How many certificates the file at name holds that OpenSSL can read, told by a context of its own: a file of
    certificates certifi's list already holds adds none to a context that has loaded it, and is no fault. Raises what
    the load raised."""
    alone = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    alone.load_verify_locations(cafile=name)
    return alone.cert_store_stats()["x509"]


def _add(made: ssl.SSLContext, name: str, what: str, remedy: str) -> None:
    """Load the certificates of the file at name into made; Unusable, said as what and remedy, where the file is not
    there, cannot be read, or holds no certificate."""
    try:
        if _held(name):
            made.load_verify_locations(cafile=name)
            return
    except (FileNotFoundError, NotADirectoryError):
        raise Unusable(f"{what} is not there: {remedy}") from None
    except (OSError, ValueError):
        pass  # what OpenSSL or the file system said is no help: the sentence below is
    raise Unusable(f"{what} holds no certificate that can be read: {remedy}")


def context(setting: str | os.PathLike[str] | None = None) -> ssl.SSLContext:
    """A client context that checks the server's name and verifies its certificate against certifi's list and then
    the file the setting tls_ca_file names, where it is given, and no others. Raises Unusable where certifi is not
    installed, or a file is not there, cannot be read, or holds no certificate."""
    made = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    made.check_hostname = True
    made.verify_mode = ssl.CERT_REQUIRED
    made.verify_flags |= FLAGS
    bundle, *named = files(setting)
    _add(made, bundle, f"certifi's certificate file {bundle!r}", INSTALL)
    for name in named:
        what = f"the file the setting {SETTING} names, {name!r},"
        _add(made, name, what, f"set {SETTING} to a file of PEM certificates")
    return made
