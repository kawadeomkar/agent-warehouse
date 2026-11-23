"""A play's variables: one JSON file, given to layer 1's child as `-e @<file>`, in which no string can be read as a
template (docs/architecture/engine.md §16.10).

ansible-core loads an extra-variables file as a trusted source, so a string in it that holds `{{`, `{%` or `{#` would be
rendered as a template - a setting would be code. `wrap(value)` puts every string leaf as `{"__ansible_unsafe": s}`:
Ansible's loader reads a file as JSON first, and its decoder (ansible._internal's legacy profile) hands such a string
on as text, without the trusted mark, whatever the file's own trust. That decoder is Ansible's internal, so the tests
(tests/unit/test_aw_variables.py), not its documentation, hold this across releases.

`write(path, document, secrets=...)` writes the file whole with the standard library, at 0600, in the run's directory
(runs/rundir.py's `path_for`): a file of its own beside the path, then put in its place, never written through a link.
It refuses, before anything is written:

- a variable whose name is no role variable's (`NAME`), and above all one that begins `ansible_`: those name the play's
  connection - `ansible_ssh_common_args`, `ansible_host_key_checking`, `ansible_ssh_executable`, `ansible_user` - and
  an extra variable outranks the inventory, where the SSH runner's options are (layer1.py);
- a key anywhere that begins `__ansible`, which Ansible's decoder reads as its own (`__ansible_unsafe`,
  `__ansible_vault`, `__ansible_type`): one it cannot read fails the JSON decoding, and Ansible's loader then reads the
  very same text as YAML, where a wrapped string is a plain mapping and its string a template it trusts;
- anything else that would fail that decoding, for the same reason: an integer of more than 64 bits (Python reads no
  more than 4300 digits), a float that is not finite, a nesting deeper than `DEPTH_MOST`; and a string no UTF-8 holds
  (a lone surrogate), which fails the YAML too, so that the play's variables would not load;
- a value of any other type than a mapping with string keys, a list or tuple, a string, an integer, a float, a boolean
  or None (a Secret above all);
- a document that holds a secret the run opened (`secrets`, the Secrets every caller names), anywhere in a name, a
  string or the file's text: no secret is ever in this file, and a refusal never says one back.

`held(path)` holds a file to what `write` makes - JSON, a mapping of role variables' names, every string wrapped and
nothing above refused - and layer1.run asks it of the file it is given before anything is written or started.

Standard library only, and nothing at import but json, math, os, re and tempfile.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

from claude_vm.secret import Secret

# How a string is marked so that Ansible's decoder hands it on as text, and what begins every key that decoder reads as
# its own.
WRAPPER = "__ansible_unsafe"
RESERVED = "__ansible"
# What begins the name of a variable that sets the play's connection, its interpreter or its become.
CONNECTION = "ansible_"
# A play's variable: a role variable's name, as the roles' argument_specs.yml name them.
NAME = re.compile(r"[a-z][a-z0-9_]*")
# How deep a value may nest, a wrapped string's own level included: a role's options are a few levels at most.
DEPTH_MOST = 32
# The integers a value may hold: 64 bits, signed.
INTEGER_MOST = 2**63

# The file's mode: the run's alone.
MODE = 0o600
# What a key the decoder reads as its own is refused with.
_RESERVED_KEY = f"no key of a play's variables begins {RESERVED}, which Ansible's decoder reads as its own"


class Refused(ValueError):
    """A document, a value or a file the variables file does not take, said by what is wrong and never with a value:
    nothing was written."""


# ---------------------------------------------------------------- the wrapping ----


def wrap(value: object) -> object:
    """value with every string leaf put as {WRAPPER: s}: a mapping's keys kept, a tuple made a list, every other leaf as
    it is. Refused: a type a variable cannot hold, a mapping whose key is not a string, a value that holds itself."""
    return _wrapped(value, ())


def _wrapped(value: object, above: tuple[int, ...]) -> object:
    if type(value) is str:
        return {WRAPPER: value}
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, Secret):
        raise Refused("a Secret is never in a play's variables: it reaches a child on its standard input")
    if isinstance(value, (Mapping, list, tuple)):
        if id(value) in above:
            raise Refused("a value of a play's variables holds itself")
        within = (*above, id(value))
        if isinstance(value, Mapping):
            if not all(type(key) is str for key in value):
                raise Refused("a mapping of a play's variables has keys that are strings alone")
            if any(key.startswith(RESERVED) for key in value):
                raise Refused(_RESERVED_KEY)
            return {key: _wrapped(item, within) for key, item in value.items()}
        return [_wrapped(item, within) for item in value]
    raise Refused(f"a play's variables hold no {type(value).__name__}: mappings, lists, strings, numbers and booleans")


# ---------------------------------------------------------------- what is held ----


def _text(value: str) -> None:
    """A string UTF-8 holds: Ansible's JSON decoding refuses one that is not (a lone surrogate), and so does the YAML
    its loader reads the file as then, so that the play's variables would not load."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused("a string of a play's variables is no text UTF-8 holds (a lone surrogate)") from None


def _hold(value: object, depth: int) -> None:
    """value, of the wrapped form, held to what Ansible's JSON decoder reads as this module means it: every string
    wrapped, no key of the decoder's own, nothing that fails the decoding."""
    if depth > DEPTH_MOST:
        raise Refused(f"a play's variables nest no deeper than {DEPTH_MOST} levels")
    if type(value) is dict:
        if WRAPPER in value:
            if set(value) != {WRAPPER} or type(value[WRAPPER]) is not str:
                raise Refused(f"a wrapped string is a mapping of {WRAPPER} to the string alone")
            _text(value[WRAPPER])
            return
        for key, item in value.items():
            if type(key) is not str or key.startswith(RESERVED):
                raise Refused(_RESERVED_KEY)
            _text(key)
            _hold(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _hold(item, depth + 1)
    elif type(value) is str:
        raise Refused("a string of a play's variables is wrapped, so that Ansible never reads it as a template")
    elif type(value) is int:
        if not -INTEGER_MOST <= value < INTEGER_MOST:
            raise Refused("an integer of a play's variables is one of 64 bits")
    elif type(value) is float:
        if not math.isfinite(value):
            raise Refused("a number of a play's variables is a finite one")
    elif value is not None and type(value) is not bool:
        raise Refused(f"a play's variables hold no {type(value).__name__}")


def _names(document: Mapping[str, object]) -> None:
    """Each variable a role variable's name, none the connection's."""
    for name in document:
        if name.startswith(CONNECTION):
            raise Refused(
                f"{name} names the play's connection, which the inventory alone gives, from the SSH runner's options:"
                f" a play's variables never hold one"
            )
        if not NAME.fullmatch(name):
            raise Refused(
                "a play's variable is named as a role variable is: lower-case letters, digits and underscores"
            )


def _strings(value: object) -> Iterator[str]:
    """Every string of a value of the wrapped form, a mapping's keys included."""
    if type(value) is dict:
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif type(value) is list:
        for item in value:
            yield from _strings(item)
    elif type(value) is str:
        yield value


def _holds_a_secret(texts: Iterable[str], secrets: tuple[Secret, ...]) -> bool:
    """Whether a text holds one of the secrets: each struck from it, never revealed, an empty one striking nothing."""
    return any(secret.struck(text) != text for text in texts for secret in secrets)


def _secrets(given: object) -> tuple[Secret, ...]:
    if isinstance(given, (str, bytes, Secret)) or not isinstance(given, Iterable):
        raise TypeError("the secrets the run opened are a list of Secrets")
    found = tuple(given)
    if not all(isinstance(secret, Secret) for secret in found):
        raise TypeError("the secrets the run opened are each a Secret")
    return found


def _content(document: Mapping[str, object], *, secrets: Iterable[Secret]) -> str:
    """The variables file's text for document, every string wrapped, each rule above held. secrets are the Secrets the
    run opened; a document that holds one anywhere is refused, its name said only where the name itself holds none."""
    opened = _secrets(secrets)
    if not isinstance(document, Mapping):
        raise Refused("a play's variables are a mapping of names to values")
    if not all(type(name) is str for name in document):
        raise Refused("a play's variables are named by strings")
    if _holds_a_secret(document, opened):
        raise Refused("a name of the play's variables holds a secret the run opened: no secret is ever in the file")
    _names(document)
    written: dict[str, object] = {}
    for name, value in document.items():
        wrapped = written[name] = wrap(value)
        _hold(wrapped, 1)
        if _holds_a_secret(_strings(wrapped), opened):
            raise Refused(f"{name} holds a secret the run opened: no secret is ever in a play's variables")
    made = json.dumps(written, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    if _holds_a_secret([made, json.dumps(written, ensure_ascii=False)], opened):
        raise Refused("a value of the play's variables holds a secret the run opened: no secret is ever in the file")
    return made


def write(path: str | os.PathLike, document: Mapping[str, object], *, secrets: Iterable[Secret]) -> Path:
    """document written to path as the play's variables file, every string wrapped and each rule above held, at MODE,
    whole: a file of its own beside path, then put in its place, so that a link at path is replaced and never written
    through. path is absolute, its folder the run's directory (rundir.path_for). Everything is held before anything is
    written."""
    if not isinstance(path, (str, os.PathLike)) or not Path(path).is_absolute():
        raise Refused("the variables file's path is an absolute one, in the run's directory")
    where = Path(path)
    if not where.parent.is_dir():
        raise Refused(f"the variables file's folder, {where.parent}, is not a folder")
    content = _content(document, secrets=secrets)
    descriptor, staged = tempfile.mkstemp(dir=where.parent, prefix=f".{where.name}.", suffix=".writing")
    try:
        os.fchmod(descriptor, MODE)
        with os.fdopen(descriptor, "w", encoding="ascii") as out:
            descriptor = -1
            out.write(content)
        os.replace(staged, where)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        Path(staged).unlink(missing_ok=True)
        raise
    return where


# ---------------------------------------------------------------- a file read back ----


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    found = dict(pairs)
    if len(found) != len(pairs):
        raise Refused("a mapping of the variables file names a key twice")
    return found


def _constant(name: str) -> object:
    raise Refused(f"the variables file holds {name}, which is no JSON number")


def held(path: str | os.PathLike) -> Path:
    """path, a variables file held to what `write` makes: JSON, a mapping of role variables' names, none the
    connection's, every string wrapped, and nothing else `write` refuses. Refused, before any child reads it, with what
    is wrong and never a value of it."""
    where = Path(path)
    try:
        content = where.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Refused(f"the variables file, {where}, cannot be read: {type(error).__name__}") from None
    try:
        document = json.loads(content, object_pairs_hook=_pairs, parse_constant=_constant)
    except Refused:
        raise
    except (ValueError, RecursionError):
        raise Refused(f"the variables file, {where}, is not the JSON a play's variables are written as") from None
    if type(document) is not dict:
        raise Refused(f"the variables file, {where}, holds a mapping of names to values")
    _names(document)
    for value in document.values():
        _hold(value, 1)
    return where
