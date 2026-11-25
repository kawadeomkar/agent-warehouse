"""A play's variables: one JSON file, given to layer 1's child as `-e @<file>`, in which no string can be read as a
template (docs/architecture/engine.md §16.10).

ansible-core loads an extra-variables file as a trusted source, so a string in it that holds `{{`, `{%` or `{#` would be
rendered as a template - a setting would be code. `wrap(value)` puts every string leaf as `{"__ansible_unsafe": s}`:
Ansible's loader reads a file as JSON first, and its decoder (ansible._internal's legacy profile) hands such a string
on as text, without the trusted mark, whatever the file's own trust. That decoder is Ansible's internal, so the tests
(tests/unit/test_aw_variables.py), not its documentation, hold this across releases.

`write(path, document, secrets=..., names=...)` writes the file whole with the standard library, at 0600, in the run's
directory (runs/rundir.py's `path_for`): a file of its own beside the path, then put in its place, never written
through a link. `names` are the names the play takes, its caller's to give and never left out: for the system play,
guest/system.py's `NAMES`, guest/wiring.py's one list. It refuses, before anything is written:

- a variable whose name has not the shape a role variable's has (`NAME`), held first, so that no refusal says a name
  with a control character;
- a variable whose name begins `ansible_`: those name the play's connection - `ansible_ssh_common_args`,
  `ansible_host_key_checking`, `ansible_ssh_executable`, `ansible_user` - and an extra variable outranks the inventory,
  where the SSH runner's options are (layer1.py);
- a variable named as one of `FIXED`: the inventory's own name of the machine (layer1.py's `NAME_VARIABLE`), Ansible's
  magic variables (`inventory_hostname`, `group_names`, `inventory_file`, ...) and the names ansible-core reserves
  (`get_reserved_names()`: `omit`, a play's and a task's keywords, Jinja's globals such as `lookup` and `range`). An
  extra variable outranks each of them for every task of the play: `omit` set would make every `default(omit)` of the
  roles pass a value, `inventory_hostname` would rename the host, `lookup` would be text no task could call. The list is
  written here, since this module imports no Ansible; tests/unit/test_aw_variables.py holds it to the locked
  ansible-core's own. These three hold whatever `names` lists;
- a variable that is none of `names`, by its name: an extra variable outranks a play's vars, a task's vars, a set_fact
  and a register too, so a name of the play's own (the system play's check that the connection is the engine's,
  `system_not_written`), a register (`configure_base`) or a role's internal (`common_names_1x`, a list of files moved as
  root) would turn the play's own logic off for every task;
- a key anywhere that begins `__ansible`, which Ansible's decoder reads as its own (`__ansible_unsafe`,
  `__ansible_vault`, `__ansible_type`): one it cannot read fails the JSON decoding, and Ansible's loader then reads the
  very same text as YAML, where a wrapped string is a plain mapping and its string a template it trusts;
- anything else that would fail that decoding, for the same reason: an integer of more than 64 bits (Python reads no
  more than 4300 digits), a float that is not finite, a nesting deeper than `DEPTH_MOST`; and a string no UTF-8 holds
  (a lone surrogate), which fails the YAML too, so that the play's variables would not load. The nesting is measured
  before anything recurses deeply, by `write`'s own walk and by a scan of a file's text before it is decoded, so the
  refusal is the same sentence whatever depth the interpreter's own JSON decoder or recursion would reach;
- a value of any other type than a mapping with string keys, a list or tuple, a string, an integer, a float, a boolean
  or None (a Secret above all);
- a document that holds a secret the run opened (`secrets`, the Secrets every caller names), anywhere in a name, a
  string or the file's text: no secret is ever in this file, and a refusal never says one back.

`held(path, names)` holds a file to what `write` makes - JSON, a mapping of the names the play takes, every string
wrapped and nothing above refused, its nesting measured on its text before it is decoded - and layer1.run asks it of the
file it is given, with the names its caller gives beside the play, before anything is written or started.

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
# The shape of a play's variable's name: a role variable's, as the roles' argument_specs.yml name them. A shape alone:
# the names a play takes are its caller's (`names`).
NAME = re.compile(r"[a-z][a-z0-9_]*")
# The names no play's variable takes, but those that begin CONNECTION, which are refused for that: the name the
# inventory gives the machine a person chose (layer1.py's NAME_VARIABLE, which this module cannot import, since layer1
# imports it); every magic variable ansible-core 2.21.6 gives a host, a play and a role's task (Host.get_magic_vars and
# VariableManager.get_vars), hostvars among them, which it gives every play; and every name its get_reserved_names()
# returns, the keywords of a play, a role, a block and a task, and the globals of its templates. Held equal to the
# locked ansible-core's by tests/unit/test_aw_variables.py, so that a release that adds one fails there.
FIXED = frozenset(
    {
        # The machine's name, as the inventory layer 1 writes gives it.
        "aw_name",
        # Ansible's magic variables.
        "environment",
        "group_names",
        "groups",
        "hostvars",
        "inventory_dir",
        "inventory_file",
        "inventory_hostname",
        "inventory_hostname_short",
        "play_hosts",
        "playbook_dir",
        "role_name",
        "role_names",
        "role_path",
        "role_uuid",
        "vars",
        # What get_reserved_names() returns: the keywords, and the templates' globals.
        "action",
        "always",
        "any_errors_fatal",
        "args",
        "async",
        "async_val",
        "become",
        "become_exe",
        "become_flags",
        "become_method",
        "become_user",
        "block",
        "changed_when",
        "check_mode",
        "collections",
        "connection",
        "cycler",
        "debugger",
        "delay",
        "delegate_facts",
        "delegate_to",
        "dict",
        "diff",
        "fact_path",
        "failed_when",
        "force_handlers",
        "gather_facts",
        "gather_timeout",
        "handlers",
        "hosts",
        "ignore_errors",
        "ignore_unreachable",
        "joiner",
        "lipsum",
        "local_action",
        "lookup",
        "loop",
        "loop_control",
        "loop_with",
        "max_fail_percentage",
        "module_defaults",
        "name",
        "namespace",
        "no_log",
        "notify",
        "now",
        "omit",
        "order",
        "poll",
        "port",
        "post_tasks",
        "pre_tasks",
        "q",
        "query",
        "range",
        "register",
        "remote_user",
        "rescue",
        "retries",
        "roles",
        "run_once",
        "serial",
        "strategy",
        "tags",
        "tasks",
        "throttle",
        "timeout",
        "undef",
        "until",
        "validate_argspec",
        "vars_files",
        "vars_prompt",
        "when",
        "with_",
    }
)
# How deep a value may nest, a wrapped string's own level included: a role's options are a few levels at most.
DEPTH_MOST = 32
# What a scan of a file's nesting reads: a JSON string whole, its escapes inside it, or a bracket outside one.
_TOKEN = re.compile(r'"(?:[^"\\]++|\\.)*+"?|[\[\]{}]', re.DOTALL)
# The integers a value may hold: 64 bits, signed.
INTEGER_MOST = 2**63

# The file's mode: the run's alone.
MODE = 0o600
# What a key the decoder reads as its own is refused with.
_RESERVED_KEY = f"no key of a play's variables begins {RESERVED}, which Ansible's decoder reads as its own"
# What a nesting past DEPTH_MOST is refused with, wherever it is found.
_TOO_DEEP = f"a play's variables nest no deeper than {DEPTH_MOST} levels"


class Refused(ValueError):
    """A document, a value or a file the variables file does not take, said by what is wrong and never with a value:
    nothing was written."""


# ---------------------------------------------------------------- the wrapping ----


def wrap(value: object) -> object:
    """value with every string leaf put as {WRAPPER: s}: a mapping's keys kept, a tuple made a list, every other leaf as
    it is. Refused: a type a variable cannot hold, a mapping whose key is not a string, and a value nested deeper than
    DEPTH_MOST, value itself at the first level - a value that holds itself among them, said so - before the walk goes
    any deeper."""
    return _wrapped(value, ())


def _wrapped(value: object, above: tuple[int, ...]) -> object:
    """value wrapped, above the containers it is in, outermost first: its level is one more than their number. Where the
    depth runs out, a container met twice on the way down is one that holds itself."""
    if len(above) >= DEPTH_MOST:
        if len(set(above)) < len(above):
            raise Refused("a value of a play's variables holds itself")
        raise Refused(_TOO_DEEP)
    if type(value) is str:
        return {WRAPPER: value}
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, Secret):
        raise Refused("a Secret is never in a play's variables: it reaches a child on its standard input")
    if isinstance(value, (Mapping, list, tuple)):
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
        raise Refused(_TOO_DEEP)
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


def _taken(names: object) -> frozenset[str]:
    """The names a play takes, as its caller gives them: a collection of strings, never one string."""
    if isinstance(names, (str, bytes)) or not isinstance(names, Iterable):
        raise Refused("the names a play takes are a collection of names, given beside its variables")
    found = list(names)
    if not all(type(name) is str for name in found):
        raise Refused("the names a play takes are each a string")
    return frozenset(found)


def _names(document: Mapping[str, object], names: frozenset[str]) -> None:
    """Each variable of a role variable's shape, none the connection's, none of FIXED, and one of the names the play
    takes: the name is held to NAME before a sentence says it."""
    for name in document:
        if not NAME.fullmatch(name):
            raise Refused(
                "a play's variable is named as a role variable is: lower-case letters, digits and underscores"
            )
        if name.startswith(CONNECTION):
            raise Refused(
                f"{name} names the play's connection, which the inventory alone gives, from the SSH runner's options:"
                f" a play's variables never hold one"
            )
        if name in FIXED:
            raise Refused(
                f"{name} is a name Ansible gives the play itself, or the inventory the machine's: a play's variables"
                f" never hold one, which would outrank it for every task"
            )
        if name not in names:
            raise Refused(
                f"{name} is no variable of the play's: its variables hold the names it takes alone, since any other"
                f" would outrank the play's own or its roles' for every task"
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


def _content(document: Mapping[str, object], *, secrets: Iterable[Secret], names: Iterable[str]) -> str:
    """The variables file's text for document, every string wrapped, each rule above held. secrets are the Secrets the
    run opened; a document that holds one anywhere is refused, its name said only where the name itself holds none.
    names are the names the play takes."""
    opened = _secrets(secrets)
    taken = _taken(names)
    if not isinstance(document, Mapping):
        raise Refused("a play's variables are a mapping of names to values")
    if not all(type(name) is str for name in document):
        raise Refused("a play's variables are named by strings")
    if _holds_a_secret(document, opened):
        raise Refused("a name of the play's variables holds a secret the run opened: no secret is ever in the file")
    _names(document, taken)
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


def write(
    path: str | os.PathLike, document: Mapping[str, object], *, secrets: Iterable[Secret], names: Iterable[str]
) -> Path:
    """document written to path as the play's variables file, every string wrapped and each rule above held, its names
    among names, the names the play takes, at MODE, whole: a file of its own beside path, then put in its place, so that
    a link at path is replaced and never written through. path is absolute, its folder the run's directory
    (rundir.path_for). Everything is held before anything is written."""
    if not isinstance(path, (str, os.PathLike)) or not Path(path).is_absolute():
        raise Refused("the variables file's path is an absolute one, in the run's directory")
    where = Path(path)
    if not where.parent.is_dir():
        raise Refused(f"the variables file's folder, {where.parent}, is not a folder")
    content = _content(document, secrets=secrets, names=names)
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


def _nesting(content: str) -> None:
    """content's nesting held to DEPTH_MOST before it is decoded: the brackets outside its strings, counted, the
    document's own mapping one more than its values' levels. A decoder reads the same strings, so it never nests deeper
    than this count, and the refusal never depends on how deep the interpreter's decoder would go; a file no deeper is
    held level by level once decoded (_hold)."""
    opened = 0
    for token in _TOKEN.finditer(content):
        mark = content[token.start()]
        if mark in "[{":
            opened += 1
            if opened > DEPTH_MOST + 1:
                raise Refused(_TOO_DEEP)
        elif mark in "]}":
            opened -= 1


def held(path: str | os.PathLike, names: Iterable[str]) -> Path:
    """path, a variables file held to what `write` makes: JSON, a mapping of the names the play takes (names), none
    the connection's, every string wrapped, and nothing else `write` refuses. Refused, before any child reads it, with
    what is wrong and never a value of it."""
    taken = _taken(names)
    where = Path(path)
    try:
        content = where.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Refused(f"the variables file, {where}, cannot be read: {type(error).__name__}") from None
    _nesting(content)
    try:
        document = json.loads(content, object_pairs_hook=_pairs, parse_constant=_constant)
    except Refused:
        raise
    except ValueError:
        raise Refused(f"the variables file, {where}, is not the JSON a play's variables are written as") from None
    if type(document) is not dict:
        raise Refused(f"the variables file, {where}, holds a mapping of names to values")
    _names(document, taken)
    for value in document.values():
        _hold(value, 1)
    return where
