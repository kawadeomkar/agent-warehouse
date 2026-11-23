"""The vendors' trust: one certificate file, named by its path, and the TLS context made from it alone
(docs/architecture/engine.md §16.7).

GitHub, Tailscale and the download servers are verified against one file of PEM certificates: certifi's bundle, by the
path certifi gives, the same on every system, unless the computer's setting tls_ca_file names another. That is a
recorded change from 2.x, whose vendor calls were Ansible's uri, verified against the system's own store of the machine
that made them, which the interpreter's certificate variables change. Neither that store nor any certificate variable
is read here: tls_ca_file is the one way to add a root, a company's say, and the refusal of a certificate that does not
verify (the transport's Untrusted) says so. There is no table of each system's paths.

context() loads that file and nothing else: not the interpreter's own store, nothing the environment names, not
macOS's keychain. It makes the context by hand, since the standard library's ready-made one loads the interpreter's
store where it is given no file, and reads the environment for a file to log the session's keys to. The proxy is not
this module's: proxy.choose gives it to the transport beside this context.
"""

from __future__ import annotations

import os
import ssl

# The computer's setting that names the vendors' certificate file in place of certifi's: every refusal names it.
SETTING = "tls_ca_file"
# What a context verifies with beside the file: the flags Python 3.13's own context sets, and today's requests on it,
# set here on every Python, so that one before 3.13 is no looser.
FLAGS = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN


class Unusable(Exception):
    """The vendors' certificate file cannot be used: it is not there, cannot be read, or holds no certificate. Its
    sentence names the setting tls_ca_file."""


def ca_file(setting: str | os.PathLike[str] | None) -> str:
    """The path of the file the vendors are verified against: the one the setting tls_ca_file names, where it is given
    (neither None nor empty), else certifi's bundle. Whether the file can be used is context()'s to say."""
    if setting is not None and os.fspath(setting):
        return os.fspath(setting)
    try:
        import certifi
    except ImportError:
        raise Unusable(
            "certifi, whose certificate file the vendors are verified against by default, is not installed: install"
            f" the checkout's environment again, or name a file of PEM certificates with the setting {SETTING}"
        ) from None
    return certifi.where()


def context(path: str | os.PathLike[str]) -> ssl.SSLContext:
    """A client context that checks the server's name and verifies its certificate against those of the file at path,
    and no others. Raises Unusable where the file is not there, cannot be read, or holds no certificate."""
    made = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    made.check_hostname = True
    made.verify_mode = ssl.CERT_REQUIRED
    made.verify_flags |= FLAGS
    name = os.fspath(path)
    remedy = f"name a file of PEM certificates with the setting {SETTING}"
    try:
        made.load_verify_locations(cafile=name)
    except (FileNotFoundError, NotADirectoryError):
        raise Unusable(f"the vendors' certificate file {name!r} is not there: {remedy}") from None
    except (OSError, ValueError):
        pass  # what OpenSSL or the file system said is no help: the sentence below is
    else:
        if made.cert_store_stats()["x509"]:
            return made
    raise Unusable(f"the vendors' certificate file {name!r} holds no certificate that can be read: {remedy}")
