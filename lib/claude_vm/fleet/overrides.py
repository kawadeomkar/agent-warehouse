"""The overrides: plain files of literals in three layers under the settings table's defaults, and the values given
with `--set` for one run above them (docs/architecture/engine.md §16.9).

A file is a YAML mapping of setting names to literals and nothing else: no comment of an example's, no expression, no
anchor or alias, no key twice, no line break inside a value. Each row of the table (fleet/registry.py) has one home,
and is read from that file alone:

- the fleet's `fleet/settings.yml`: a row of home `fleet`, at its top level, and one of scope `machine` in its map
  `machines`, by `<host>/<machine>`, never at its top level, which would give every machine one value;
- each host's `fleet/hosts/<name>/settings.yml`: a row of home `host`, that host's own, which no other host reads;
- this computer's `client.yml`: a row of home `client`, at its top level; one of scope `host` in its map `hosts`, by the
  host's name, and never at its top level, since each host's is its own; and one of scope `machine` (`vm_ssh_options`)
  at its top level for every machine and in its map `machines` for one, which outranks it. Beside them, its
  `fleet_home` (fleet/paths.py's) is kept as it is, and its `current_host` (the hosts', fleet/hosts.py) is written by
  `set_current` alone.

A row of home `run` is in no file: it is given with `--set` alone. A setting in a file that is not its home is refused
by name, with the line it is on and where it belongs. A role's option passed through (fleet/passthrough.py) is taken
where a row's name is, at home `fleet` and scope `global`: at the top of the fleet's `settings.yml`, or with `--set`,
held to its role's own spec; one of the roles' options that is no setting is refused by name with why.

`read(home, host)` returns the three files of a host; `resolve(home, host, run)` the value of every row with the
layer that set it, one of `LAYERS`: the table's default, then the file of the row's home, then the run's; and after
them, each option passed through that the fleet's file or the run sets, which has no default here. `typed(row,
text)` holds typed text to a row's type, choices, form and least value, a path's rule (`~` the user's home, a path
still relative refused by name, `""` unset) and `network_proxy` to vendors/proxy.py's one check of it, which refuses a
user and a password; a file's value is held the same way. `run_values(texts, secrets=...)` holds the words of `--set`;
`set` and `unset` write one value, and `set_current` client.yml's current host, under `store.locked`, compare-and-swap
on the digest of the file as the caller read it. `literals(path)` is the one reader of a file of literals the fleet
keeps, a host's host.yml too. Every refusal is a `Refused`, whose sentence never says a value back: a secret pasted in
the wrong place would be on the screen. A machine's recorded choices, the design's third layer, are in the machine's
record, which is not read here.

Standard library, the table and the options passed through at import; the YAML reader is imported by the functions
that read or write a file, and vendors/proxy.py by the one that holds network_proxy.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import inspect
import json
import math
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from claude_vm import store
from claude_vm.fleet import files as fleet_files
from claude_vm.fleet import passthrough, paths, registry
from claude_vm.fleet.registry import Row
from claude_vm.schema import patterns
from claude_vm.secret import Secret

# Where a value comes from, each above the one before: the table's default, the fleet's settings.yml, the host's own,
# this computer's client.yml, and a value given with --set for this run alone.
LAYERS = ("default", "fleet", "host", "client", "run")
DEFAULT, FLEET, HOST, CLIENT, RUN = LAYERS
# The overrides' file in the fleet's folder and in each host's, and the folder of the hosts.
SETTINGS = "settings.yml"
HOSTS = "hosts"
# The two maps under a file's top level: client.yml's by a host's name, and the fleet's and client.yml's by
# <host>/<machine>.
MAPS = {FLEET: ("machines",), HOST: (), CLIENT: ("hosts", "machines")}
# client.yml's keys that are no setting, each another module's, read here and kept as they are by every write but
# set_current's, which writes current_host.
CLIENT_OWN = ("fleet_home", "current_host")
# What a file of overrides holds, one and all, as a refusal says it.
SETTING = ("setting", "settings")
# A file that is not there reads as an empty one with this digest, which no file's content has.
MISSING = ""
# The most a file of overrides may hold: more is no settings file.
MOST = 1 << 20
# What a value may never hold: an expression, which the system layer would template; and a control character or a line
# break, which no setting of one line holds (YAML reads NEL and the two Unicode separators as line breaks too).
TEMPLATE = re.compile(r"\{\{|\{%|\{#")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
EXPRESSION = "takes a value, never an expression ({{ }}, {% %} or {# #})"
# Typed text for a boolean, in either case, as today's settings take it.
TRUE = ("true", "yes", "on", "y", "t", "1")
FALSE = ("false", "no", "off", "n", "f", "0")
# A whole number typed: at most 18 digits, more than any setting takes, so that int() never meets Python's limit of
# digits, whose error says the value.
WHOLE = re.compile(r"[+-]?[0-9]{1,18}")
# A size with its unit, as deploy's --disk reads one, the unit in either case: a whole number that starts with no 0.
SIZE = re.compile(r"(?P<number>[1-9][0-9]{0,5})(?P<unit>[GTgt])")
# One machine's name in a map's key, after its host's and a /.
MACHINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# What each type takes, as a refusal says it.
TAKES = {
    "bool": "true or false (yes or no, on or off)",
    "int": "a whole number",
    "str": "one line of text",
    "path": "a path that starts with / or ~ (a relative path is not taken), or nothing",
    "list[str]": "a list of strings: comma-separated (a,b) or as YAML ([a, b])",
    "dict": "a mapping, as YAML ({key: value}) or key=value,key=value",
    "size": "a whole number with G or T after it, capital or small (128G)",
}


class Refused(ValueError):
    """A value, a file or a write that is refused: the sentence says why, and never says a value back."""


class _Off(ValueError):
    """What a value lacks, said after the setting's name: the caller says where the value came from, but where the
    sentence ends with its own way out (``ends``)."""

    def __init__(self, said: str, *, ends: bool = False) -> None:
        super().__init__(said)
        self.ends = ends


# The one setting held by another module's check: the proxy's, which vendors/proxy.py's choose makes too.
NETWORK_PROXY = "network_proxy"


@dataclass(frozen=True)
class Value:
    """A setting's value for a run, and the layer that set it: one of LAYERS."""

    name: str
    value: object
    layer: str


@dataclass(frozen=True)
class File:
    """One file of overrides as it was read: its path; the digest of its bytes, MISSING where it is not there, which a
    write is given back to compare; the settings at its top level; its map hosts, by a host's name (client.yml's
    alone); its map machines, by <host>/<machine>; and client.yml's keys that are no setting (CLIENT_OWN), fleet_home
    as fleet/paths.py reads it and current_host."""

    path: Path
    digest: str
    settings: Mapping[str, object]
    hosts: Mapping[str, Mapping[str, object]]
    machines: Mapping[str, Mapping[str, object]]
    own: Mapping[str, object]


@dataclass(frozen=True)
class Layers:
    """The three files a host's values are read from: the fleet's, the host's own (None with no host) and client.yml."""

    fleet: File
    host: File | None
    client: File


@dataclass(frozen=True)
class _Place:
    """Where one row's value is written: the file, which home's it is, and the keys of the map it is under, if any."""

    path: Path
    kind: str
    under: tuple[str, ...]


# ---------------------------------------------------------------- holding a value to its row ----


def _one_line(text: str) -> None:
    if CONTROL.search(text):
        raise _Off("takes one line of text, without a control character")
    if TEMPLATE.search(text):
        raise _Off(EXPRESSION)


def _literal(value: object, depth: int = 0) -> object:
    """A mapping's value as YAML reads one, each string one line with no expression, copied out: a string, a whole or
    finite number, a boolean, nothing, or a list or a mapping of strings to them."""
    if depth > 32:
        raise _Off("takes a mapping nested less deep than that")
    if isinstance(value, str):
        _one_line(value)
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Off("takes a finite number where it takes one")
        return value
    if isinstance(value, (list, tuple)):
        return [_literal(item, depth + 1) for item in value]
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _Off("takes a mapping whose keys are strings")
            _one_line(key)
            out[key] = _literal(item, depth + 1)
        return out
    raise _Off("takes the literals YAML reads: strings, numbers, booleans, lists and mappings")


def _expanded(text: str, environ: Mapping[str, str]) -> str:
    """A path of this computer's: "" unset; ~ or ~/ at its start the user's home; else absolute, or refused."""
    if text == "":
        return ""
    if text == "~" or text.startswith("~/"):
        try:
            text = str(paths.user_home(environ)) + text[1:]
        except paths.Refused as error:
            raise _Off(f"takes a path from your home, which is not known here ({error})") from None
    if not os.path.isabs(text):
        raise _Off("takes a path that starts with / or ~: a relative path is not taken")
    return text


def _held(row: Row, value: object, environ: Mapping[str, str]) -> object:
    """value - a literal a file holds, or typed text made one - held to row's type and choices, in the form it is kept:
    a size with its unit capital, a path's ~ made the user's home, a list or a mapping copied. Raises _Off."""
    kind = row.type
    takes = _Off(f"takes {_takes(row)}")
    if kind == "bool":
        if not isinstance(value, bool):
            raise takes
        return value
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise takes
        if row.least is not None and value < row.least:
            raise _Off(f"takes {_takes(row)} of at least {row.least}")
        return value
    if kind == "list[str]":
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise takes
        for item in value:
            _one_line(item)
        return list(value)
    if kind == "dict":
        if not isinstance(value, Mapping):
            raise takes
        return _literal(value)
    if not isinstance(value, str):
        raise takes
    _one_line(value)
    if kind == "path":
        return _expanded(value, environ)
    if kind == "size":
        size = SIZE.fullmatch(value)
        if size is None:
            raise takes
        return size["number"] + size["unit"].upper()
    if row.choices and value not in row.choices:
        raise takes
    if row.form is not None and not row.form.allows(value):
        raise takes
    if row.name == NETWORK_PROXY:
        _proxy_held(value)
    return value


def _proxy_held(value: str) -> None:
    """network_proxy's value held by the one check of the setting, vendors/proxy.py's setting_refusal: a URL that names
    a user or a password refused with its own way out, and anything but its two words and an http:// URL with a host
    refused. Imported here alone, for this one setting."""
    from claude_vm.vendors import proxy

    refused = proxy.setting_refusal(value)
    if refused == proxy.CREDENTIALS:
        raise _Off(refused, ends=True)
    if refused is not None:
        raise _Off(refused)


def _takes(row: Row) -> str:
    if row.choices:
        return f"one of {', '.join(row.choices)}"
    return row.form.said if row.form is not None else TAKES[row.type]


def typed(row: Row, text: str, *, environ: Mapping[str, str] | None = None) -> object:
    """text, as a person typed it (after --value or --set), as the value row takes, in the form it is kept; or Refused,
    with a sentence that names the setting and never says the value. An expression and a control character are refused
    in the text, and again in the value it parses to, where YAML's escapes could make one."""
    if not isinstance(text, str):
        raise TypeError(f"typed text is a str, not a {type(text).__name__}")
    env = os.environ if environ is None else environ
    try:
        _one_line(text)
        kind = row.type
        if kind == "bool":
            if text.lower() not in TRUE + FALSE:
                raise _Off(f"takes {_takes(row)}")
            value: object = text.lower() in TRUE
        elif kind == "int":
            if WHOLE.fullmatch(text) is None:
                raise _Off(f"takes {_takes(row)}")
            value = int(text)
        elif kind == "list[str]":
            value = _flow(text, list) if text.lstrip().startswith("[") else _items(text)
        elif kind == "dict":
            value = _flow(text, dict) if text.lstrip().startswith("{") else _pairs(text)
        else:
            value = text
        return _held(row, value, env)
    except _Off as off:
        raise Refused(f"{row.name} {off}" + ("" if off.ends else ": the value given is not one")) from None


def _items(text: str) -> list[str]:
    """A list typed comma-separated: each item with the white space around it taken off; nothing typed, no item."""
    if not text.strip():
        return []
    items = [item.strip() for item in text.split(",")]
    if not all(items):
        raise _Off("takes a list with no empty item")
    return items


def _pairs(text: str) -> dict[str, str]:
    """A mapping typed as key=value,key=value; nothing typed, no key."""
    if not text.strip():
        return {}
    out: dict[str, str] = {}
    for pair in text.split(","):
        key, sep, value = pair.partition("=")
        if not sep or not key.strip() or key.strip() in out:
            raise _Off(f"takes {TAKES['dict']}, each key once")
        out[key.strip()] = value.strip()
    return out


def _flow(text: str, kind: type) -> object:
    """A list or a mapping typed as YAML, read as a file is: no anchor, alias, merge or key twice."""
    import yaml

    try:
        value = _construct(text)
    except yaml.YAMLError:
        raise _Off(f"takes {TAKES['list[str]' if kind is list else 'dict']}: what is typed is not YAML") from None
    if not isinstance(value, kind):
        raise _Off(f"takes {TAKES['list[str]' if kind is list else 'dict']}")
    return value


# ---------------------------------------------------------------- the values for one run ----


def _unknown(name: str) -> str:
    """Why a name is refused, never saying it back - a secret pasted for a name would be on the screen - but naming
    the settings close to it, the role options passed through among them."""
    names = [each.name for each in registry.rows()] + [each.name for each in passthrough.options() if not each.refused]
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.75)
    return "that is no setting's name" + (f" (did you mean {' or '.join(close)}?)" if close else "")


def _row_or_option(name: str) -> Row | None:
    """The row of a setting, or the row a role's option passed through is read by; None for any other name."""
    try:
        return registry.row(name)
    except KeyError:
        pass
    try:
        return passthrough.row(name)
    except KeyError:
        return None


def _row(name: str) -> Row:
    """The row of a setting or of a role's option passed through; Refused for a role's option that is no setting, by
    its name and why, and for any other name, which is never said back."""
    found = _row_or_option(name)
    if found is not None:
        return found
    why = passthrough.refusal(name)
    raise Refused(f"{name} {why}" if why is not None else _unknown(name))


def _holds_a_secret(texts: Iterable[str], secrets: Iterable[Secret]) -> bool:
    """Whether a text holds one of the vault's secrets: each struck from it, never revealed, an empty one striking
    nothing."""
    return any(secret.struck(text) != text for secret in secrets for text in texts)


def _strings(value: object) -> list[str]:
    """Every string a value holds, a mapping's keys too, and its JSON, which YAML's escapes cannot hide one from."""
    found = [json.dumps(value, ensure_ascii=False)]
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            found += [key, *_strings(item)]
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _strings(item)
    return found


def run_values(
    texts: Iterable[str], *, secrets: Iterable[Secret], environ: Mapping[str, str] | None = None
) -> dict[str, object]:
    """The values `--set name=value` gives for this run, by name: each word as the parser took it (the pattern
    `assignment`), its name a row's, its value typed by `typed`. These are what the run records as its resolved inputs
    and what `resolve` puts on top. Refused, with no value in the sentence: a name that is no setting's, never said back
    either; a row that may hold a secret, which the run's record would keep; a value that holds a secret the vault holds
    (`secrets`, which every caller names: the vault's, or none where it holds none), looked for in the text and again
    in the value it parses to; an expression; and a value its row does not take. A name given twice is refused too."""
    secrets = tuple(secrets)
    if not all(isinstance(secret, Secret) for secret in secrets):
        raise TypeError("the vault's secrets are each a Secret")
    found: dict[str, object] = {}
    for text in texts:
        name, sep, raw = str(text).partition("=")
        if not sep or not patterns.full("setting_name", name):
            raise Refused("--set takes a setting's name, then = and its value for this run")
        row = _row(name)
        if row.secret:
            raise Refused(f"{name} may hold a secret, which the run's record would keep: it is never given for one run")
        if _holds_a_secret([raw], secrets):
            raise Refused(
                f"that value of {name} is a secret the vault holds, which the run's record would keep: not used"
            )
        value = typed(row, raw, environ=environ)
        if _holds_a_secret(_strings(value), secrets):
            raise Refused(
                f"that value of {name} is a secret the vault holds, which the run's record would keep: not used"
            )
        if name in found:
            raise Refused(f"--set gives {name} more than once: give it once")
        found[name] = value
    return found


# ---------------------------------------------------------------- reading a file ----

# What YAML reads but cannot make a value of, as a refusal says it, never with the value: `!!int x`, `!!bool x`, a
# date that is none (`2026-02-30`), a number past Python's limit of digits, a nesting past its recursion.
UNREADABLE = "found a value YAML cannot read"


def _loader():
    """PyYAML's safe loader, refusing an anchor, an alias, a merge and a key given twice: a file of literals has none,
    and an alias is how a small file is made to read as a large one; and a scalar it cannot make a value of
    (UNREADABLE), by its line."""
    import yaml

    class Literals(yaml.SafeLoader):
        def compose_node(self, parent, index):
            event = self.peek_event()
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise yaml.composer.ComposerError(None, None, "found an anchor or an alias", event.start_mark)
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            seen = []
            for key_node, _value in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise yaml.constructor.ConstructorError(None, None, "found a merge", key_node.start_mark)
                key = self.construct_object(key_node, deep=True)
                if key in seen:
                    raise yaml.constructor.ConstructorError(None, None, "found a key given twice", key_node.start_mark)
                seen.append(key)
            return super().construct_mapping(node, deep=deep)

        def construct_object(self, node, deep=False):
            # The safe constructor raises Python's own error, which may say the value, for a scalar it cannot make one
            # of: refused here, by the node's line. An integer past Python's limit of digits is one: written in
            # hexadecimal, octal or binary it is read past the limit, and a write of the file or a printer fails on it.
            try:
                data = super().construct_object(node, deep=deep)
                if type(data) is int:
                    str(data)
            except yaml.YAMLError:
                raise
            except Exception:
                raise yaml.constructor.ConstructorError(None, None, UNREADABLE, node.start_mark) from None
            return data

    return Literals


def _construct(text: str) -> object:
    """text read as one YAML document of literals: None where it is empty. What YAML raises but its own errors - a
    nesting past Python's recursion while it composes, say - is one of its errors here, which says no value."""
    import yaml

    loader = _loader()(text)
    try:
        return loader.get_single_data()
    except yaml.YAMLError:
        raise
    except Exception:
        raise yaml.MarkedYAMLError(problem=UNREADABLE) from None
    finally:
        loader.dispose()


def _lines(text: str) -> dict[tuple[str, ...], int]:
    """The line each key of a document's mapping is on, its maps' and their entries' too, by the keys that lead to it;
    and under (), the line the document's value starts on."""
    loader = _loader()(text)
    found: dict[tuple[str, ...], int] = {}

    def walk(node, under: tuple[str, ...]) -> None:
        import yaml

        if not isinstance(node, yaml.MappingNode) or len(under) > 2:
            return
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                found.setdefault((*under, key_node.value), key_node.start_mark.line + 1)
                walk(value_node, (*under, key_node.value))

    try:
        root = loader.get_single_node()
        if root is not None:
            found[()] = root.start_mark.line + 1
            walk(root, ())
    finally:
        loader.dispose()
    return found


def _bytes(path: Path, what: tuple[str, str] = SETTING) -> bytes | None:
    """The file's bytes, at most MOST of them; None where it is not there. what names what the file holds, as a
    refusal says it (SETTING, or fleet/hosts.py's for a host's host.yml)."""
    many = what[1]
    try:
        with open(path, "rb") as stream:
            data = stream.read(MOST + 1)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refused(f"{path} cannot be read ({error.strerror or error}): the {many} are not known") from None
    if len(data) > MOST:
        raise Refused(f"{path} holds more than a file of {many} does ({MOST} bytes at most): the {many} are not known")
    return data


def _digest(data: bytes | None) -> str:
    return MISSING if data is None else hashlib.sha256(data).hexdigest()


def _raw(text: str, path: Path, what: tuple[str, str] = SETTING) -> tuple[dict, dict[tuple[str, ...], int]]:
    """A file's text as a plain mapping, with the line each key is on: refused where it is no mapping of literals.
    what names one of what the file holds and all of them, as a refusal says them."""
    import yaml

    one, many = what
    found = TEMPLATE.search(text)
    if found is not None:
        raise Refused(f"{path} line {text.count(chr(10), 0, found.start()) + 1}: a {one} {EXPRESSION}")
    try:
        data = _construct(text)
        lines = _lines(text)
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark or error.context_mark
        line = f" line {mark.line + 1}" if mark is not None else ""
        raise Refused(f"{path}{line}: this is not a file of {many} ({error.problem or error.context})") from None
    except yaml.YAMLError:
        raise Refused(f"{path}: this is not a file of {many}, which is YAML") from None
    if data is None:
        return {}, {}
    if not isinstance(data, dict):
        raise Refused(f"{path} line {lines.get((), 1)}: this is not a mapping of {many} to their values")
    return data, lines


def literals(path: Path, what: tuple[str, str] = SETTING) -> tuple[dict, dict[tuple[str, ...], int]]:
    """A file of literals the fleet keeps, as a plain mapping, with the line each key is on by the keys that lead to
    it: the one reader of such a file, the three files of overrides and a host's host.yml (fleet/hosts.py) alike. Empty
    where the file is not there. Refused, by its path and line, where it holds more than MOST bytes, is not UTF-8,
    holds an expression anywhere, or is no mapping of literals: an anchor, an alias, a merge, a key given twice or a
    second document among it. what names one of what the file holds and all of them, as a refusal says them."""
    data = _bytes(path, what)
    if data is None:
        return {}, {}
    return _raw(_text(data, path, what), path, what)


def _text(data: bytes, path: Path, what: tuple[str, str] = SETTING) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{path} is not UTF-8 text: the {what[1]} are not known") from None


def _belongs(row: Row) -> str:
    """Where a row is kept, as a refusal of it elsewhere says."""
    if row.home == RUN:
        return "it is given for one run alone, with --set, and kept in no file"
    if row.home == HOST:
        return f"it is each host's own: keep it in its {HOSTS}/<host>/{SETTINGS} in the fleet's folder"
    if row.home == FLEET:
        if row.scope == "machine":
            return (
                f"it is each machine's own: keep it in the fleet's {SETTINGS}, in its map machines by <host>/<machine>"
            )
        return f"it is the fleet's: keep it at the top of the fleet's {SETTINGS}"
    if row.scope == "host":
        return "it is this computer's own for each host: keep it in client.yml, in its map hosts by the host's name"
    if row.scope == "machine":
        return "it is this computer's own: keep it at the top of client.yml, or in its map machines by <host>/<machine>"
    return "it is this computer's own: keep it at the top of client.yml"


def _here(row: Row, kind: str, under: str | None) -> bool:
    """Whether a file of kind keeps row at its top level (under None) or in its map under."""
    if row.home != kind:
        return False
    if kind == FLEET:
        return (row.scope == "machine") == (under == "machines")
    if kind == HOST:
        return under is None
    if under == "hosts":
        return row.scope == "host"
    if under == "machines":
        return row.scope == "machine"
    return row.scope in ("global", "machine")


def _key_named(key: object) -> str:
    """A file's key as a refusal says it: by name where it is one a setting could have, else not at all."""
    return key if isinstance(key, str) and patterns.full("setting_name", key) else "a key"


def _machine_key(host: str, machine: str) -> str:
    if not patterns.full("current_host", host) or MACHINE.fullmatch(machine) is None:
        raise Refused("a machine's settings are kept under <host>/<machine>: that host or machine is no name of one")
    return f"{host}/{machine}"


def _settings(
    data: object, path: Path, lines: dict, kind: str, under: tuple[str, ...], environ: Mapping[str, str]
) -> dict[str, object]:
    """The settings of a mapping at under in a file of kind, each held to its row and kept where its home is."""
    at = f"{path} line {lines.get(under, 1)}"
    if not isinstance(data, dict):
        raise Refused(f"{at}: {under[-1] if under else 'this'} is not a mapping of settings to their values")
    found: dict[str, object] = {}
    for key, value in data.items():
        line = f"{path} line {lines.get((*under, key), 1) if isinstance(key, str) else 1}"
        if not isinstance(key, str) or not patterns.full("setting_name", key):
            raise Refused(f"{line}: {_key_named(key)} that is no setting's name")
        row = _row_or_option(key)
        if row is None:
            why = passthrough.refusal(key)
            raise Refused(f"{line}: {key} {why if why is not None else 'is no setting'}")
        if not _here(row, kind, under[0] if under else None):
            raise Refused(f"{line}: {key} is not kept here: {_belongs(row)}")
        if value is None:
            raise Refused(f"{line}: {key} is empty there: give it a value, or take its line out")
        try:
            found[key] = _held(row, value, environ)
        except _Off as off:
            raise Refused(f"{line}: {key} {off}" + ("" if off.ends else ": the value there is not one")) from None
    return found


def _map(
    data: dict, path: Path, lines: dict, kind: str, name: str, environ: Mapping[str, str]
) -> dict[str, dict[str, object]]:
    """A file's map name: each entry's key held to a host's name (hosts) or <host>/<machine> (machines), and its
    settings."""
    entries = data.get(name, {})
    if not isinstance(entries, dict):
        raise Refused(f"{path} line {lines.get((name,), 1)}: {name} is not a mapping")
    found: dict[str, dict[str, object]] = {}
    for key, settings in entries.items():
        line = f"{path} line {lines.get((name, key), 1) if isinstance(key, str) else 1}"
        if not isinstance(key, str):
            raise Refused(f"{line}: a key of {name} is no name")
        if name == "hosts" and not patterns.full("current_host", key):
            raise Refused(f"{line}: a key of hosts is no host's name: {patterns.PATTERNS['current_host'].words}")
        if name == "machines":
            host, sep, machine = key.partition("/")
            if not sep or not patterns.full("current_host", host) or MACHINE.fullmatch(machine) is None:
                raise Refused(f"{line}: a key of machines is not <host>/<machine>, a host's name and a machine's")
        found[key] = _settings(settings, path, lines, kind, (name, key), environ)
    return found


def _pointed(text: str, path: Path, environ: Mapping[str, str]) -> Path | None:
    """The fleet's home client.yml's one fleet_home line names, as fleet/paths.py reads it, before YAML reads the file:
    what that module refuses is refused here in its words."""
    try:
        return paths.pointer_in(text, path, environ)
    except paths.Refused as error:
        raise Refused(str(error)) from None


def _own(data: dict, pointed: Path | None, path: Path, lines: dict, environ: Mapping[str, str]) -> dict[str, object]:
    """client.yml's keys that are no setting: fleet_home, as YAML reads it, held equal to what fleet/paths.py read from
    its one line, so that this file and the fleet's home never read it apart; and current_host, a host's name."""
    home = data.get("fleet_home")
    read: Path | None = None
    if isinstance(home, str) and home:
        try:
            read = Path(os.path.normpath(_expanded(home, environ)))
        except _Off:
            read = None
    if ("fleet_home" in data and home is not None and read is None) or read != pointed:
        raise Refused(
            f"{path} line {lines.get(('fleet_home',), 1)}: fleet_home is not read as one line fleet_home: /the/folder"
            " at the start of a line, which is how the fleet's home is found: write it so"
        )
    current = data.get("current_host")
    if current is not None and not (isinstance(current, str) and patterns.full("current_host", current)):
        raise Refused(
            f"{path} line {lines.get(('current_host',), 1)}: current_host is no host's name:"
            f" {patterns.PATTERNS['current_host'].words}"
        )
    return {"fleet_home": pointed, "current_host": current}


def _file(path: Path, data: bytes | None, kind: str, environ: Mapping[str, str]) -> tuple[File, dict]:
    """A file of kind as read from its bytes, and its plain mapping, which a write changes."""
    text = "" if data is None else _text(data, path)
    pointed = _pointed(text, path, environ) if kind == CLIENT else None
    raw, lines = _raw(text, path)
    maps = MAPS[kind]
    top = {key: value for key, value in raw.items() if key not in maps and not (kind == CLIENT and key in CLIENT_OWN)}
    file = File(
        path=path,
        digest=_digest(data),
        settings=MappingProxyType(_settings(top, path, lines, kind, (), environ)),
        hosts=MappingProxyType(_map(raw, path, lines, kind, "hosts", environ) if "hosts" in maps else {}),
        machines=MappingProxyType(_map(raw, path, lines, kind, "machines", environ) if "machines" in maps else {}),
        own=MappingProxyType(_own(raw, pointed, path, lines, environ) if kind == CLIENT else {}),
    )
    return file, raw


def _host_name(host: str) -> str:
    if not isinstance(host, str) or not patterns.full("current_host", host):
        raise Refused(f"that is no host's name: {patterns.PATTERNS['current_host'].words}")
    return host


def files(home: paths.Home, host: str | None) -> tuple[Path, Path | None, Path]:
    """The three files a host's values are read from: the fleet's settings.yml, the host's own (None with no host) and
    client.yml."""
    fleet = paths.fleet_dir(home)
    return (
        fleet / SETTINGS,
        None if host is None else fleet / HOSTS / _host_name(host) / SETTINGS,
        paths.client_file(home),
    )


def read(home: paths.Home, host: str | None, *, environ: Mapping[str, str] | None = None) -> Layers:
    """The three layers of overrides a host's run reads, each file held whole: a file that is not there is empty, and
    one that holds what it may not is refused, by its path and line, before anything is used."""
    env = os.environ if environ is None else environ
    fleet, own, client = files(home, host)
    return Layers(
        fleet=_file(fleet, _bytes(fleet), FLEET, env)[0],
        host=None if own is None else _file(own, _bytes(own), HOST, env)[0],
        client=_file(client, _bytes(client), CLIENT, env)[0],
    )


# ---------------------------------------------------------------- every value, and the layer that set it ----


def _from_files(row: Row, layers: Layers, host: str | None, machine: str | None) -> tuple[str, object] | None:
    """The value of row its home's file sets for host and machine, with the layer; None where none does."""
    key = None if host is None or machine is None else _machine_key(host, machine)
    if row.home == FLEET:
        found = layers.fleet.machines.get(key, {}) if row.scope == "machine" else layers.fleet.settings
        return (FLEET, found[row.name]) if row.name in found else None
    if row.home == HOST:
        settings = {} if layers.host is None else layers.host.settings
        return (HOST, settings[row.name]) if row.name in settings else None
    if row.home == CLIENT:
        if row.scope == "host":
            found = {} if host is None else layers.client.hosts.get(host, {})
        elif row.scope == "machine" and row.name in layers.client.machines.get(key, {}):
            found = layers.client.machines[key]
        else:
            found = layers.client.settings
        return (CLIENT, found[row.name]) if row.name in found else None
    return None


def _copied(value: object) -> object:
    """A table's default as a reader may change it: a tuple a list, a read-only mapping a dict, at every depth."""
    if isinstance(value, tuple):
        return [_copied(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _copied(item) for key, item in value.items()}
    return value


def resolve(
    home: paths.Home,
    host: str | None,
    run: Mapping[str, object] = MappingProxyType({}),
    *,
    machine: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Value]:
    """Every row's value for a run against host (and, for a row of scope machine, machine), with the layer that set
    it: the value run gives (`run_values`' result), else the one its home's file sets, else the table's default. A
    default worked out from something else is worked out here: from another row's value, the machine's name, this
    computer's user home, or the home's key file. With no machine named, a default made from the machine's name is None:
    it has a value only for a machine. In the table's order; after the rows, each role's option passed through that the
    run or the fleet's file sets, in fleet/passthrough.py's order: one set nowhere has no value here, since its role's
    own default applies where the role runs."""
    env = os.environ if environ is None else environ
    layers = read(home, host, environ=env)
    if host is not None and machine is not None:
        _machine_key(host, machine)
    given: dict[str, object] = {}
    for name, value in run.items():
        row = _row(name)
        if row.secret:
            raise Refused(f"{name} may hold a secret, which the run's record would keep: it is never given for one run")
        try:
            given[name] = _held(row, value, env)
        except _Off as off:
            raise Refused(f"{name} {off}" + ("" if off.ends else ": the value given for this run is not one")) from None
    found: dict[str, Value] = {}
    for row in registry.rows():
        if row.name in given:
            found[row.name] = Value(row.name, given[row.name], RUN)
        elif (set_by := _from_files(row, layers, host, machine)) is not None:
            found[row.name] = Value(row.name, set_by[1], set_by[0])

    def needed(name: str, seen: tuple[str, ...]) -> object:
        """What a computed default reads, by its parameter's name: one of registry.GIVEN, or another row's value."""
        if name == "machine":
            return machine
        if name == "user_home":
            return paths.user_home(env)
        if name == "key_file":
            return paths.key_file(home)
        return worked_out(registry.row(name), seen)

    def worked_out(row: Row, seen: tuple[str, ...]) -> object:
        if row.name in found:
            return found[row.name].value
        if row.name in seen:
            raise ValueError(f"the defaults of {', '.join(seen)} are each worked out from another of them")
        if callable(row.default):
            reads = list(inspect.signature(row.default).parameters)
            value = (
                None
                if "machine" in reads and machine is None
                else row.default(**{name: needed(name, (*seen, row.name)) for name in reads})
            )
        else:
            value = _copied(row.default)
        found[row.name] = Value(row.name, value, DEFAULT)
        return value

    for row in registry.rows():
        worked_out(row, ())
    resolved = {row.name: found[row.name] for row in registry.rows()}
    for option in passthrough.options():
        if option.refused is not None:
            continue
        if option.name in given:
            resolved[option.name] = Value(option.name, given[option.name], RUN)
        elif (set_by := _from_files(passthrough.row(option.name), layers, host, machine)) is not None:
            resolved[option.name] = Value(option.name, set_by[1], set_by[0])
    return resolved


# ---------------------------------------------------------------- writing one value ----


def _place(home: paths.Home, row: Row, host: str | None, machine: str | None) -> _Place:
    """The file and the map a row's value is written in, for host and machine."""
    if row.home == RUN:
        raise Refused(f"{row.name} is given for one run alone, with --set: no file keeps it")
    if row.home == FLEET and row.scope == "machine" and machine is None:
        raise Refused(f"{row.name} is each machine's own: name the machine it is for")
    fleet, _host_file, client = files(home, None)
    path, kind = (fleet, FLEET) if row.home == FLEET else (client, CLIENT)
    if row.home == HOST or row.scope == "host" or (row.scope == "machine" and machine is not None):
        if host is None:
            raise Refused(f"{row.name} is kept for each host or each machine: name the host it is for")
        if row.home == HOST:
            return _Place(fleet.parent / HOSTS / _host_name(host) / SETTINGS, HOST, ())
        if row.scope == "host":
            return _Place(path, kind, ("hosts", _host_name(host)))
        return _Place(path, kind, ("machines", _machine_key(host, machine or "")))
    return _Place(path, kind, ())


def _dumped(raw: dict) -> str:
    """A plain mapping as a file of settings: one key on each line, block style, nothing folded; empty, nothing."""
    import yaml

    if not raw:
        return ""
    # ASCII alone, YAML's escapes for the rest, so that the bytes written are the same whatever the locale's encoding.
    return yaml.safe_dump(raw, default_flow_style=False, sort_keys=False, allow_unicode=False, width=1 << 30)


def _folders(home: paths.Home, place: _Place) -> None:
    """The folders a file is written in: a host's must be there already, since it is the host; the home's and the
    fleet's folder are made through fleet/files.py's folder, at paths.DIR_MODE where they are not, and a link where
    one goes is refused."""
    if place.kind == HOST:
        if not place.path.parent.is_dir():
            raise Refused(
                f"the fleet has no host of that name: {place.path.parent} is not there, so nothing was written"
            )
        return
    for folder in dict.fromkeys((home.root, place.path.parent)):
        try:
            fleet_files.folder(folder)
        except fleet_files.Unsafe as unsafe:
            raise Refused(str(unsafe).removeprefix("error: ")) from None
        except OSError as error:
            raise Refused(f"{folder} cannot be made ({error.strerror or error}): nothing was written") from None


def _write(
    home: paths.Home,
    place: _Place,
    change: Callable[[dict], None],
    expect: str,
    environ: Mapping[str, str],
    own: Mapping[str, object] = MappingProxyType({}),
) -> str:
    """The one writer of a file of overrides: under store.locked, only while the file is still as the caller read it
    (its digest is expect), changed by change, written whole, read back the same - client.yml's keys that are no
    setting read back as they were but for what own says they become, fleet_home read the same by fleet/paths.py too -
    and replaced atomically. Returns the digest of what the file holds after. A change that writes nothing into a file
    whose folder is not there makes no folder."""
    if not place.path.parent.is_dir():
        nothing: dict = {}
        change(nothing)
        if not nothing:
            if expect != MISSING:
                raise Refused(f"{place.path} changed since it was read: nothing was written, so read it again")
            return MISSING
    _folders(home, place)
    with store.locked(place.path):
        data = _bytes(place.path)
        if _digest(data) != expect:
            raise Refused(f"{place.path} changed since it was read: nothing was written, so read it again")
        before, raw = _file(place.path, data, place.kind, environ)
        after = copy.deepcopy(raw)
        change(after)
        if after == raw:
            return before.digest
        text = _dumped(after)
        written, again = _file(place.path, text.encode("utf-8"), place.kind, environ)
        if again != after or (place.kind == CLIENT and dict(written.own) != {**before.own, **own}):
            raise Refused(f"{place.path} would not read back as it is meant to once written: nothing was written")
        store.replace(place.path, text, mode_if_new=paths.FILE_MODE)
        return written.digest


def set(
    home: paths.Home,
    name: str,
    value: object,
    host: str | None,
    expect: str,
    *,
    machine: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Write one setting's value into the file of its home, for host (and machine, for a row of scope machine; with
    none, client.yml's vm_ssh_options for every machine), a role's option passed through at the top of the fleet's
    settings.yml, held to its row first: compare-and-swap on expect, the digest of the file as the caller read it
    (File.digest), refused with nothing written where the file changed since. The file's other keys are kept as they
    are. Returns the digest of the file after it."""
    env = os.environ if environ is None else environ
    row = _row(name)
    try:
        kept = _held(row, value, env)
    except _Off as off:
        raise Refused(f"{name} {off}: nothing was written") from None
    place = _place(home, row, host, machine)

    def change(raw: dict) -> None:
        within = raw
        for key in place.under:
            within = within.setdefault(key, {})
        within[name] = kept

    return _write(home, place, change, expect, env)


def unset(
    home: paths.Home,
    name: str,
    host: str | None,
    expect: str,
    *,
    machine: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Take one setting out of the file of its home, as set finds it, so that its default applies again: a map left
    empty goes with it. Compare-and-swap on expect, as set. Returns the digest of the file after it."""
    env = os.environ if environ is None else environ
    row = _row(name)
    place = _place(home, row, host, machine)

    def change(raw: dict) -> None:
        chain = [raw]
        for key in place.under:
            if key not in chain[-1]:
                return
            chain.append(chain[-1][key])
        if name not in chain[-1]:
            return
        del chain[-1][name]
        # Each map it leaves empty goes, the innermost first.
        for key, parent in zip(reversed(place.under), reversed(chain[:-1]), strict=True):
            if parent[key] == {}:
                del parent[key]

    return _write(home, place, change, expect, env)


def set_current(home: paths.Home, host: str, expect: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Write client.yml's current_host, this computer's current host: a host's name, held to it here as the reader
    holds it, and to a host of the fleet that can be used by its one caller, fleet/hosts.py's make_current, first.
    Compare-and-swap on expect, as set; the file's settings and fleet_home are kept as they are. Returns the digest of
    the file after it."""
    env = os.environ if environ is None else environ
    name = _host_name(host)
    place = _Place(paths.client_file(home), CLIENT, ())

    def change(raw: dict) -> None:
        raw["current_host"] = name

    return _write(home, place, change, expect, env, own={"current_host": name})
