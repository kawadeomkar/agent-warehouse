"""aw settings: each setting's value and the layer that set it, for one group, one name, every setting or what the
files change, from the settings table (fleet/registry.py) under the overrides (fleet/overrides.py), with no Ansible;
and the writes of one typed value, --value, --reset and edit (docs/architecture/engine.md §16.9).

`settings` is the handler words.py's row of settings names: --value is `write`'s, --reset `reset`'s, the word edit
`edit`'s, and anything else a read, `show`'s. The reads' forms, each on screen or, with --json, as one document alone
on standard output with today's keys:

- alone: how to use it, and the groups, each role with its spec's description and how many settings it lists;
- a group (a role, `common`, `connection`): its rows, and for a role every option of its spec that no row names
  (fleet/passthrough.py's options), each passed through with its value, or said why it is no setting;
- a name: its value, the layer and the file that set it, its default, the line that changes it and what it does;
- --all: every row of the table, each option the fleet's file sets, and each name of the vault's closed list
  (fleet/secrets.py), set or not set;
- --changed: only what the files change, alone all of it but the vault, which it does not open, with a group that
  group's;
- --host <name>: that host's values, where the current host's are read otherwise.

Every row names its home: the fleet's settings.yml, a host's, this computer's client.yml (the computer's own settings,
shown beside the fleet's as today's settings shows local.yml's), or a value for one run alone. A secret is shown by
its row's kind alone, never by its value: a row that may hold one (`secret`), an option its role marks the vault's,
and each name of the vault's list, which is said set or not set. --all alone opens the vault, with the key the
settings name (fleet/vaultkey.py), to read which names it holds, and says so in one line where it cannot; no other read
opens it. No read writes a file or starts a child.

The writes change one setting in the file that is its home, through overrides.set and overrides.unset, compare-and-swap
on the digest read at the start, under the file's short lock those take, and no lock of a run. What a name can be
written as comes from one function, `settable`: a secret, a name the vault holds, a row that may hold a secret, an
option its role marks the vault's or one set through a row, an option made from each machine's own name, one a run
sets for itself, a value given for one run alone, the host's address and its other fields, and an internal value are
each refused with today's sentence, which names aw vault where today's named just vault. A value is typed by
overrides.typed, which refuses an expression and a control character in the text and in the value it parses to; before
that and after it, a value that holds a secret the vault holds is refused, the vault opened for it with its key, and
never said back. A file the overrides' reader refuses (an inline !vault among what it refuses) is never written over.
After a write it says what applies it, as today. `edit` writes every setting it can write, each under its
description, to a file of the editor's own, and on return applies each changed line as the same typed write, all or
none: a line refused is said by its number and the file is offered again. It refuses with no terminal.

The line builders are today's tools/settings' (`shown`, `source`, `table`, `tail`, `one`), less everything that asked
Ansible. Standard library, the table, the overrides and today's wiring at import; the YAML reader through the
overrides, and pyrage through the vault's key, for --all and the writes alone.
"""

from __future__ import annotations

import difflib
import inspect
import json as json_text
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from claude_vm import text, wiring
from claude_vm.fleet import hosts, overrides, passthrough, paths, registry, secrets
from claude_vm.hosts import model
from claude_vm.schema import patterns, printer
from claude_vm.schema.printer import said
from claude_vm.schema.words import EXITS, PLACEHOLDER, Line
from claude_vm.secret import Secret
from claude_vm.tui import screen

# The program's root, where fleet/paths.py works out the fleet's home from: a checkout or an installed release.
PROGRAM_ROOT = Path(__file__).resolve().parents[3]
# The width every line is held to.
WIDTH = 100
# What each group that is no role is, as the usage says it.
GROUPS_SAID = {"connection": "this computer's own SSH to the machines: its options and its jump"}
# Where a vault's name is kept, as a row says it.
VAULT_FILE = "fleet/vault.age"
# The settings verb's options, as the usage's JSON lists them.
OPTIONS = ("NAME", "--all", "--changed", "--json", "--host", "--value", "--reset")
# The word that opens the settings in an editor, in place of a setting's name: no setting is named so.
EDIT = "edit"
# The roles configure.yml runs, which deploy --only configure --tags <role> applies (today's configured_roles; held
# to playbooks/configure.yml by tests/unit/test_aw_settings_verb.py).
CONFIGURED = ("common", "vscode_server", "dev_tools", "claude_code", "github_projects", "tailscale")
# Today's names of the host's address and its hop's account, each a field of the host's own host.yml now.
HOST_NAMES = {"ansible_host": "address", "vm_ssh_jump": "jump"}

# The lines a sentence here names.
_SET = Line("settings", {"name": PLACEHOLDER, "value": PLACEHOLDER})
_VAULT = Line("vault", {"name": PLACEHOLDER})
_ONE = Line("settings", {"name": PLACEHOLDER})
_ALL = Line("settings", {"all": True})
_CHANGED = Line("settings", {"changed": True})
_JSON = Line("settings", {"name": PLACEHOLDER, "json": True})
_RESET = Line("settings", {"name": PLACEHOLDER, "reset": True})
_EDIT = Line("settings", {"name": EDIT})
_STATUS = Line("status", host_flag="")

# The sentences, each said through the printer of a command.
UNKNOWN = "error: that is neither a group ({groups}) nor a setting or a role's option{close}"
TAIL = "Change a setting with {set}, a secret with {vault}."
USAGE = (
    "Each setting's value and where it was set, for one group, one setting or all of them.",
    "Reads without changing anything. A secret is never shown: it is set or not set.",
)
USAGE_LINES = (
    ("  {form}  every setting of one group, or one setting explained", {"form": _ONE}),
    ("  {form}  every setting, and which secrets of the vault are set", {"form": _ALL}),
    ("  {form}  only what your files change, the vault aside: alone, all of it", {"form": _CHANGED}),
    ("  {form}  the same, as JSON on standard output", {"form": _JSON}),
    ("  {form}  set one, and {reset} puts one back", {"form": _SET, "reset": _RESET}),
    ("  {form}  every setting in your editor, each changed line written", {"form": _EDIT}),
)
VAULT_UNREAD = "the vault was not opened, so whether each of its secrets is set is not known here: "

# Why a name is not written, each said after the name where it names one (settable), and where it changes instead.
IN_THE_VAULT = "is held in the vault"
VAULT_NAMED = "a name that starts vault_ is the vault's"
SECRET_ROW = "may hold a secret, so it is never typed or said back"
ADDRESS = "the Proxmox host's address is a field of the host's own host.yml"
JUMP = "the hop's account is set on the Proxmox host's own entry in its host.yml, as jump"
HOST_FIELD = "is a field of the host's own host.yml"
INTERNAL = "is no setting: it is internal to the project"
FOR_ONE_RUN = "is given for one run alone, with --set: no file keeps it"
# What a write says: a value that would put a secret in the clear, a vault it cannot hold the value against, the
# shell's history, a value already written, and how the file is written.
IN_THE_CLEAR = "that value is a secret the vault holds, so {name} would hold it in the clear: not written"
VAULT_SHUT = "the vault cannot be opened, so the value cannot be held against the secrets it holds: nothing was written"
HISTORY = ". The value given was not used or printed, but your shell's history keeps it"
ALREADY = "{file} sets it already: nothing was written"
AT_DEFAULT = "{file} does not set {name}, so it is at its default already: nothing was written"
WHOLE = "the file is written whole, so no comment in it is kept"
FLEET_WIDE = "{name} is the fleet's, one value for every host: --host does not change where it is written"
NOW = "{name} is now {value}, from {source}"
# What applies a value, by the roles of the variables it sets (today's applies).
CONFIGURES = "{deploy} applies it to every VM"
CREATED = (
    "a VM gets it when it is created: one that exists keeps what it was made with ({status} shows one that differs)"
)
BUILT = "the template gets it when it is built: one that exists stays as it is"
NEXT_RUN = "the next run uses it"
# edit's sentences.
NO_TERMINAL = "error: {edit} opens your editor, at a terminal: none is here, so nothing was opened"
EDIT_ALONE = "error: {edit} opens every setting in your editor: it takes --host alone"
NO_EDITOR = "error: the editor cannot be run ({why}): set VISUAL or EDITOR to one; nothing was written"
EDITOR_FAILED = "error: the editor ended with exit {code}: nothing was written"
NOT_TEXT = "error: what the editor left is not UTF-8 text: nothing was written"
NOT_A_LINE = "not one setting's name, then a colon and its value"
TWICE = "{name} is given on line {first} too: give it once"
AGAIN = "Open it again to put them right? [Y/n] "
UNCHANGED = "Nothing was changed: nothing was written."
EDIT_HEAD = (
    "Every setting that --value writes, for {host}.",
    "Each line is a setting's name, a colon and its value as --value takes it; a line that starts with # is none.",
    "Take a setting's line out, or put # before it, and it goes back to its default. Nothing is written until the",
    "editor ends, and then only if every changed line can be: a line refused is said by its number.",
)


class _Refused(Exception):
    """A read that cannot be made: its sentence, said as the run's error."""


def _relative(home: paths.Home, path: Path) -> str:
    try:
        return str(path.relative_to(home.root))
    except ValueError:
        return str(path)


def _row_named(name: str) -> registry.Row | None:
    try:
        return registry.row(name)
    except KeyError:
        return None


def _per_machine(row: registry.Row) -> bool:
    """Whether row's default is made from the machine's name, which a read for no machine cannot say."""
    return callable(row.default) and "machine" in inspect.signature(row.default).parameters


def _host_flag(host: str | None, many: bool, row_home: str, scope: str) -> str:
    """The host's flag of a line that changes a row: the host's name where the row is each host's and the fleet has
    several."""
    return host if host and many and (row_home == overrides.HOST or scope == "host") else ""


class Read:
    """One read of the fleet's settings, for one host: the files, every value with its layer, and the rows built."""

    def __init__(self, home: paths.Home, host: str | None, many: bool, environ: Mapping[str, str]):
        self.home = home
        self.host = host
        self.many = many
        self.environ = environ
        self.values = overrides.resolve(home, host, environ=environ)
        fleet, own, client = overrides.files(home, host)
        self.where = {
            overrides.DEFAULT: "default",
            overrides.FLEET: _relative(home, fleet),
            overrides.HOST: _relative(home, own) if own is not None else "a host's settings.yml",
            overrides.CLIENT: _relative(home, client),
            overrides.RUN: "--set, for this run",
        }
        self.opened: dict[str, Any] | None = None
        self.unread = ""

    # ------------------------------------------------------------------------------------------- the rows ----

    def _base(self, name: str, role: str | None) -> dict[str, Any]:
        return {
            "name": name,
            "role": role,
            "kind": "value",
            "value": None,
            "secret": None,
            "set_in": "default",
            "home": None,
            "via": [],
            "evaluated_as": self.host,
            "changed": False,
            "change": None,
            "why": None,
            "entry_point": None,
            "set_by_run": [],
            "wiring": None,
            "wiring_in": None,
        }

    def _change(self, name: str, row_home: str, scope: str) -> str:
        return printer.line(
            "settings",
            {"name": name, "value": PLACEHOLDER},
            refused=False,
            host_flag=_host_flag(self.host, self.many, row_home, scope),
        )

    def row(self, each: registry.Row) -> dict[str, Any]:
        """A row of the table, with its value and the layer that set it."""
        value = self.values[each.name]
        found = self._base(each.name, each.group)
        found |= {"set_in": self.where[value.layer], "home": each.home, "changed": value.layer != overrides.DEFAULT}
        if each.secret:
            empty = value.value in ("", None, [], {})
            found |= {"kind": "secret", "secret": "not set" if empty else "set"}
            found["why"] = "it may hold a secret, so it is never shown, typed or said back"
        elif value.layer == overrides.DEFAULT and value.value is None and _per_machine(each):
            found["kind"] = "per_vm"
        else:
            found["value"] = value.value
        if each.home == overrides.RUN:
            found["why"] = "it is given for one run alone, with --set, and kept in no file"
        elif not each.secret:
            found["change"] = self._change(each.name, each.home, each.scope)
        return found

    def option(self, each: passthrough.Option) -> dict[str, Any]:
        """A role's option no row names: passed through, with its value where the fleet's file sets it and its role's
        own default where nothing does; or why it is no setting, with the value of the row it is set through."""
        found = self._base(each.name, each.role) | {"entry_point": each.entry_point, "home": overrides.FLEET}
        if each.refused is not None:
            found["why"] = each.refused
            through = each.through
            if through is not None and through in self.values:
                row = self.row(registry.row(through))
                found |= {key: row[key] for key in ("kind", "value", "secret", "changed", "change")}
                found["via"] = [{"name": through, "file": row["set_in"]}]
                found["set_in"] = row["set_in"]
            elif each.refused == passthrough.SECRET:
                found["kind"] = "secret"
            else:
                found["kind"] = "no_setting"
            return found
        found["change"] = self._change(each.name, overrides.FLEET, "global")
        if each.name in self.values:
            value = self.values[each.name]
            found |= {"value": value.value, "set_in": self.where[value.layer], "changed": True}
            return found
        has, default = passthrough.default(each)
        if not has:
            found["kind"] = "unset"
        elif isinstance(default, str) and overrides.TEMPLATE.search(default):
            found |= {"kind": "on_the_vm", "wiring": default, "wiring_in": f"roles/{each.role}/defaults/main.yml"}
        else:
            found["value"] = default
        return found

    def vault_row(self, each: secrets.Row) -> dict[str, Any]:
        """A name of the vault's closed list: set or not set, where the vault was opened; never its value."""
        found = self._base(each.name, None) | {"kind": "secret", "set_in": VAULT_FILE, "home": "vault"}
        found["section"] = "Secrets"
        found["secret"] = None if self.opened is None else ("set" if self._held(each) else "not set")
        found["changed"] = found["secret"] == "set"
        flag = self.host if each.scope == "host" and self.host and self.many else ""
        found["change"] = printer.line("vault", {"name": each.name}, refused=False, host_flag=flag)
        return found

    def _held(self, each: secrets.Row) -> bool:
        assert self.opened is not None
        if each.scope == "global":
            return secrets.entry(each.name) in self.opened
        if each.scope == "host":
            return self.host is not None and secrets.entry(each.name, host=self.host) in self.opened
        return any(secrets.parsed(key)[0].name == each.name for key in self.opened)

    def open_vault(self) -> None:
        """The vault opened with its key, to read which names it holds: what it cannot open is said once, as a note."""
        from claude_vm.fleet import sealed, vaultkey

        try:
            key = vaultkey.open(
                self.home,
                key_file=self.values["vault_key_file"].value,
                key_command=self.values["vault_key_command"].value,
                environ=self.environ,
            )
            self.opened = dict(sealed.read(self.home, key) or {})
        except (vaultkey.Refused, sealed.Refused) as error:
            self.unread = VAULT_UNREAD + str(error).removeprefix("error: ")

    # ------------------------------------------------------------------------------------------- the forms ----

    def group(self, name: str) -> list[dict[str, Any]]:
        """A group's rows, in the table's order; for a role, then every option of its spec no row names."""
        rows = [self.row(each) for each in registry.rows() if each.group == name]
        rows += [self.option(each) for each in passthrough.options() if each.role == name]
        return rows

    def everything(self, vault: bool) -> list[dict[str, Any]]:
        """Every row, each with its group as its section, and each option the fleet's file sets; with vault, the vault
        opened and each of its names, set or not set."""
        rows = [self.row(each) | {"section": each.group} for each in registry.rows()]
        rows += [
            self.option(each) | {"section": each.role}
            for each in passthrough.options()
            if each.refused is None and each.name in self.values
        ]
        if not vault:
            return rows
        self.open_vault()
        return rows + [self.vault_row(each) for each in secrets.ROWS]

    def one(self, name: str) -> dict[str, Any] | None:
        """One name's row, with what it does and its default; None for a name that is neither."""
        each = _row_named(name)
        if each is not None:
            found = self.row(each) | {"description": each.description}
            if callable(each.default):
                found["default"] = None
                found["default_said"] = text.one_line(inspect.getdoc(each.default) or "", 400)
            elif not each.secret:
                found["default"] = registry.plain(each.default)
            return found
        try:
            option = passthrough.option(name)
        except KeyError:
            option = None
        if option is not None:
            found = self.option(option) | {"description": option.description}
            has, default = passthrough.default(option)
            if has and found["kind"] != "secret":
                found["default"] = default
            return found
        listed = secrets.row(name)
        if listed is not None:
            # A name of the vault's: whether it is set is --all's to say, which opens the vault.
            return self.vault_row(listed) | {"description": listed.description, "secret": None}
        return None


# ---------------------------------------------------------------------------------------------- the words ---


def shown(row: Mapping[str, Any]) -> str:
    """A row's value as the table shows it: a secret as set or not set, never its value."""
    kind = row["kind"]
    if kind == "secret":
        return {"set": "set (secret)", "not set": "not set"}.get(row["secret"], "a secret, never shown")
    said_of = {
        "per_vm": "the machine's name",
        "on_the_vm": "worked out where the role runs",
        "unset": "the role's own: none",
        "no_setting": "no setting",
    }
    if kind in said_of:
        return said_of[kind]
    value = row["value"]
    if value == "":
        return '""'
    if value is None:
        return "none"
    return text.one_line(json_text.dumps(value, ensure_ascii=False), 200)


def source(row: Mapping[str, Any]) -> tuple[str, str]:
    """Where a row was set, and its style: a person's own file in their colour, a default dim."""
    if row["via"]:
        first = row["via"][0]
        return f"through {first['name']}, {first['file']}", "y" if first["file"] != "default" else "d"
    where = row["set_in"]
    return where, "m" if row["home"] == "vault" else "d" if where == "default" else "y"


def table(rows: Sequence[Mapping[str, Any]], heading) -> list[list]:
    """Rows under their headings, every line within WIDTH: a value longer than its column is cut with "…" (the
    one-name view shows it whole), and a source that does not fit beside it goes whole to the next line."""
    lines: list[list] = []
    names = min(max((len(row["name"]) for row in rows), default=10), 44)
    values = max(8, min(26, max((len(shown(row)) for row in rows), default=8)))
    current = None
    for row in rows:
        title = heading(row)
        if title != current:
            current = title
            lines.append([screen.seg(title, "h")])
        value = screen.cut([screen.seg(shown(row), "m" if row["kind"] == "secret" else "")], values)
        start = [screen.seg(f"  {row['name']:<{names}}  "), *screen.pad(value, values), screen.seg("  ")]
        where, style = source(row)
        if screen.width(start) + len(where) <= WIDTH:
            lines.append([*start, screen.seg(where, style)])
        else:
            lines += [
                screen.cut(start, WIDTH),
                [screen.seg("      "), screen.seg(text.one_line(where, WIDTH - 6), style)],
            ]
    return lines


def tail(rows: Sequence[Mapping[str, Any]]) -> list[list]:
    mine = sum(1 for row in rows if row.get("changed") and row["kind"] != "secret")
    kept = sum(1 for row in rows if row.get("changed") and row["home"] == "vault")
    counted = f"{screen.plural(mine, 'setting')} set in your files"
    if any(row["home"] == "vault" for row in rows):
        counted += f", {screen.plural(kept, 'secret')} set in the vault"
    return [[], [screen.seg(f"{counted}.", "d")], [screen.seg(said(TAIL, {"set": _SET, "vault": _VAULT}), "d")]]


def _field(label: str, words: str, style: str = "") -> list[list]:
    """A labelled field of the one-name view, wrapped under its value."""
    pieces = screen.fill(words, WIDTH - 12)
    return [
        [screen.seg(f"  {label:<8}  " if index == 0 else " " * 12), screen.seg(piece, style)]
        for index, piece in enumerate(pieces)
    ]


def one(row: Mapping[str, Any]) -> list[list]:
    """One setting or option, explained: its value, where it was set, its default, the line that changes it, why it is
    no setting where it is none, and what it does."""
    where = f"role {row['role']}, entry point {row['entry_point']}" if row.get("entry_point") else row["role"] or ""
    lines = [[screen.seg(row["name"], "h"), screen.seg(f"  {where} · kept in {row['home']}", "d")]]
    lines += _field("value", shown(row), "m" if row["kind"] == "secret" else "")
    link, style = source(row)
    lines += _field("set in", text.one_line(link, 400), style)
    if row.get("wiring"):
        lines += _field("", f"{json_text.dumps(row['wiring'])} in {row['wiring_in']}", "d")
    if row.get("default_said"):
        lines += _field("default", row["default_said"])
    elif "default" in row:
        default = '""' if row["default"] == "" else text.one_line(json_text.dumps(row["default"]), 200)
        lines += _field("default", default)
    if row["why"]:
        lines += _field("why", row["why"])
    if row["change"]:
        # The line whole on a line of its own: a line is never wrapped or cut.
        lines.append([screen.seg("  change    "), screen.seg(row["change"])])
    for piece in screen.fill(text.one_line(row.get("description") or "", 4000), WIDTH - 2):
        lines.append([screen.seg(f"  {piece}")])
    return lines


def _print(lines: list[list]) -> None:
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    for line in lines:
        print(screen.render(line, color))


def _groups() -> list[tuple[str, str, int]]:
    """Each group, the table's first, then each role no row is in: its name, what it is, and how many it lists."""
    described = passthrough.described()
    names = list(dict.fromkeys([*registry.GROUPS, *described]))
    options = passthrough.options()
    return [
        (
            name,
            described.get(name) or GROUPS_SAID.get(name, ""),
            sum(1 for each in registry.rows() if each.group == name) + sum(1 for each in options if each.role == name),
        )
        for name in names
    ]


def usage(as_json: bool) -> int:
    """How to use it: the groups, each role's from its spec so a new one shows up by itself, and the options."""
    groups = _groups()
    if as_json:
        roles = [{"role": name, "description": about, "variables": count} for name, about, count in groups]
        print(json_text.dumps({"roles": roles, "options": list(OPTIONS)}, indent=2))
        return EXITS["done"]
    lines = [[screen.seg(USAGE[0])], [screen.seg(USAGE[1], "d")], []]
    lines.append([screen.seg("Groups", "h"), screen.seg(" " * 77), screen.seg("settings", "d")])
    for name, about, count in groups:
        lines.append([screen.seg(f"  {name:<18} {text.one_line(about, 66):<66}{count:>4}")])
    lines += [[], [screen.seg("Usage", "h")]]
    lines += [[screen.seg(said(sentence, filled))] for sentence, filled in USAGE_LINES]
    _print(lines)
    return EXITS["done"]


def _unknown(name: str) -> str:
    """Why a name is refused, never saying it back - a secret pasted for a name would be on the screen - but naming
    the known ones close to it."""
    names = [
        *registry.GROUPS,
        *(each.name for each in registry.rows()),
        *(each.name for each in passthrough.options()),
        *secrets.names(),
    ]
    close = difflib.get_close_matches(name, sorted(set(names)), n=3, cutoff=0.75)
    return said(
        UNKNOWN,
        {
            "groups": ", ".join(name for name, _about, _count in _groups()),
            "close": f" (did you mean {' or '.join(close)}?)" if close else "",
        },
    )


def _host(home: paths.Home, given: str | None, environ: Mapping[str, str]) -> tuple[str | None, bool]:
    """The host whose values are read, by the hosts' rules (--host, the current host, the fleet's one host), and
    whether the fleet has several; no host where the fleet has none yet and none is named. A host that lacks a setting
    it sets for itself is taken: that is what the settings put right."""
    layout = hosts.Layout(home, environ)
    if not given and not layout.folders():
        return None, False
    reached = hosts.reach(layout, model.Request(host=given or "", lenient=True))
    for note in reached.notes:
        print(f"{note.level}: {note.text}", file=sys.stderr)
    return reached.host.name, layout.many()


def _refused(sentence: str) -> int:
    print(f"error: {sentence.removeprefix('error: ')}", file=sys.stderr)
    return EXITS["refused"]


def show(
    run: Any,
    *,
    name: str | None = None,
    all: bool = False,
    changed: bool = False,
    json: bool = False,
    host: str | None = None,
) -> int:
    """aw settings's reads, each form its documented output (the module's docstring). Returns the exit status."""
    every = all is True or (changed is True and not name)
    if not name and not every:
        return usage(json is True)
    environ = os.environ
    try:
        home = paths.home(PROGRAM_ROOT, environ)
        where, many = _host(home, host, environ)
        read = Read(home, where, many, environ)
        if every:
            rows, target = read.everything(vault=all is True), {"kind": "all"}
        elif name in registry.GROUPS or name in passthrough.described():
            rows, target = read.group(name), {"kind": "role", "name": name}
        else:
            found = read.one(str(name))
            if found is None:
                raise _Refused(_unknown(str(name)))
            rows, target = [found], {"kind": "variable", "name": name}
    except model.Refused as refused:
        for note in refused.notes:
            print(f"{note.level}: {note.text}", file=sys.stderr)
        return _refused(str(refused))
    except (overrides.Refused, paths.Refused, _Refused) as refused:
        return _refused(str(refused))
    if changed is True:
        rows = [row for row in rows if row["changed"]]
    notes = [read.unread] if read.unread else []
    if json is True:
        report = {"target": target, "changed_only": changed is True, "settings": rows, "notes": notes}
        print(json_text.dumps(report, indent=2, ensure_ascii=False))
        return EXITS["done"]
    if target["kind"] == "variable" and not rows:
        lines = [[screen.seg(f"{name} is at its default: your files do not change it.", "d")]]
    elif target["kind"] == "variable":
        lines = one(rows[0])
    else:

        def heading(row: Mapping[str, Any]) -> str:
            if "section" in row:
                return str(row["section"])
            if (row["entry_point"] or "main") == "main":
                return str(row["role"])
            return f"{row['role']} · {row['entry_point']}"

        lines = table(rows, heading) if rows else [[screen.seg("Nothing here is changed from the defaults.", "d")]]
        lines += tail(rows)
    lines += [[], *([screen.seg(piece, "d")] for note in notes for piece in screen.fill(note, WIDTH))] if notes else []
    _print(lines)
    return EXITS["done"]


# --------------------------------------------------------------------------------------------- the writes ---

# Where a name settable refuses changes instead: with aw vault, in the host's own host.yml, or in the file of its home.
VAULT, HOST_YML, FILE = "vault", "host.yml", "file"
# The one name of the vault's that is an identifier, never looked for in a value: an OAuth client's ID, as today's
# list of what is hunted leaves out the identifiers. A secret shorter than LEAST is never looked for either, as today:
# one that short is in every other value.
IDENTIFIERS = ("tailscale_oauth_client_id",)
LEAST = 4


def settable(name: str) -> tuple[registry.Row | None, str, str]:
    """What --value, --reset and edit write for a name, or why not: (its row, "", ""), or (None, why, where it changes
    instead: VAULT, HOST_YML, FILE or ""). The one function that decides it, as today's tools/settings' settable: a
    name the vault holds, the host's address and its other fields, an option that is no setting (passthrough's why: a
    secret, set through a row, made from each machine's name, set by a run), an internal value, a row that may hold a
    secret, one given for one run alone and one that is each machine's own are refused. Why says a name back only where
    it is a name this program knows, never one the person alone typed."""
    if name in HOST_NAMES:
        return None, ADDRESS if HOST_NAMES[name] == "address" else JUMP, HOST_YML
    if secrets.row(name) is not None:
        return None, f"{name} {IN_THE_VAULT}", VAULT
    if name.startswith(passthrough.VAULT_NAME):
        known = secrets.row(name.removeprefix(passthrough.VAULT_NAME)) is not None
        return None, f"{name} {IN_THE_VAULT}" if known else VAULT_NAMED, VAULT
    row = _row_named(name)
    if row is None:
        try:
            row = passthrough.row(name)
        except KeyError:
            why = passthrough.refusal(name)
            if why is not None:
                return None, f"{name} {why}", VAULT if why == passthrough.SECRET else ""
            if name in hosts.FIELDS:
                return None, f"{name} {HOST_FIELD}", HOST_YML
            if name in wiring.wiring()[0]:
                return None, f"{name} {INTERNAL}", ""
            return None, _unknown(name), ""
    if row.secret:
        return None, f"{name} {SECRET_ROW}", FILE
    if row.home == overrides.RUN:
        return None, f"{name} {FOR_ONE_RUN}", ""
    if row.home == overrides.FLEET and row.scope == "machine":
        return None, f"{name} {passthrough.PER_MACHINE}", ""
    return row, "", ""


def _why_not(read: Read, name: str, why: str, where: str) -> str:
    """A refusal of settable's, with where the setting changes instead."""
    if where == VAULT:
        listed = secrets.row(name) or secrets.row(name.removeprefix(passthrough.VAULT_NAME))
        vault = Line("vault", {"name": listed.name}) if listed is not None else Line("vault")
        return said("{why}: change it with {vault}", {"why": why, "vault": vault})
    if where == HOST_YML:
        layout = hosts.Layout(read.home, read.environ)
        file = _relative(read.home, layout.hosts_dir / (read.host or "<name>") / hosts.HOST_FILE)
        return f"{why}: change it in {file}"
    if where == FILE:
        return f"{why}: change it in {read.where[overrides.FLEET]}"
    return why


def _hunted(read: Read) -> tuple[Secret, ...]:
    """Every secret the vault holds that a value is held against, the vault opened with its key; none where there is
    no vault. A vault that cannot be opened refuses the write: what it holds cannot be told from what is typed."""
    from claude_vm.fleet import sealed, vaultkey

    if not os.path.lexists(sealed.path(read.home)):
        return ()
    try:
        key = vaultkey.open(
            read.home,
            key_file=read.values["vault_key_file"].value,
            key_command=read.values["vault_key_command"].value,
            environ=read.environ,
        )
        opened = sealed.read(read.home, key) or {}
    except (vaultkey.Refused, sealed.Refused) as error:
        raise _Refused(f"{VAULT_SHUT} ({str(error).removeprefix('error: ')})") from None
    return tuple(
        value
        for entry, value in opened.items()
        if secrets.parsed(entry)[0].name not in IDENTIFIERS and len(value.reveal().strip()) >= LEAST
    )


def _typed(read: Read, row: registry.Row, given: str, held: Sequence[Secret]) -> object:
    """given as row's value (overrides.typed), refused where it holds a secret the vault holds: looked for in the text
    first and again in the value it parses to, which YAML's escapes could spell another way. Never says it back."""
    if overrides.holds_a_secret([given], held):
        raise _Refused(IN_THE_CLEAR.format(name=row.name))
    value = overrides.typed(row, given, environ=read.environ)
    if overrides.holds_a_secret(overrides.strings(value), held):
        raise _Refused(IN_THE_CLEAR.format(name=row.name))
    return value


def _file(read: Read, layers: overrides.Layers, row: registry.Row) -> tuple[str, str]:
    """The file a row is written in, as a sentence names it, and its digest as it was read at the start."""
    if row.home == overrides.HOST:
        return read.where[overrides.HOST], layers.host.digest if layers.host is not None else overrides.MISSING
    if row.home == overrides.CLIENT:
        return read.where[overrides.CLIENT], layers.client.digest
    return read.where[overrides.FLEET], layers.fleet.digest


def _start(host: str | None) -> tuple[Read, overrides.Layers]:
    """The read a write starts from: the host's values, and its files with the digest of each."""
    environ = os.environ
    home = paths.home(PROGRAM_ROOT, environ)
    where, many = _host(home, host, environ)
    return Read(home, where, many, environ), overrides.read(home, where, environ=environ)


def applies(name: str, host_flag: str) -> list[str]:
    """What puts a setting to use, by the roles of the variables it sets, as today's settings says it."""
    _defaults, feeds = wiring.wiring()
    specs = wiring.specs(wiring.argument_specs())
    roles = {wiring.role_of(each, specs) for each in {name, *wiring.set_by(name, feeds, specs)}} - {None}
    found = []
    configured = [role for role in CONFIGURED if role in roles]
    if configured:
        deploy = Line("deploy", {"only": "configure", "tags": ",".join(configured)}, host_flag=host_flag)
        found.append(said(CONFIGURES, {"deploy": deploy}))
    if "proxmox_vm" in roles:
        found.append(said(CREATED, {"status": _STATUS}))
    if "proxmox_template" in roles:
        found.append(BUILT)
    return found or [NEXT_RUN]


def _told(
    key: str, result: Mapping[str, Any], now: Mapping[str, Any] | None, notes: list[str], head: str, as_json: bool
) -> int:
    """A change said: as JSON under key, with the row as it is now and the notes, or its head line, then its notes."""
    if as_json:
        print(json_text.dumps({key: result, "now": now, "notes": notes}, indent=2, ensure_ascii=False))
        return EXITS["done"]
    lines = [[screen.seg(piece, "y")] for piece in screen.fill(head, WIDTH)] if head else []
    for note in notes:
        lines += [[screen.seg("  " + piece)] for piece in screen.fill(note, WIDTH - 2)]
    _print(lines)
    return EXITS["done"]


def _refusal(refused: Exception) -> int:
    """A write's refusal said, the notes of a host that cannot be reached with it."""
    if isinstance(refused, model.Refused):
        for note in refused.notes:
            print(f"{note.level}: {note.text}", file=sys.stderr)
    return _refused(str(refused))


REFUSALS = (model.Refused, overrides.Refused, paths.Refused, _Refused)


def write(run: Any, *, name: str, value: str, host: str | None = None, json: bool = False) -> int:
    """--value: the one setting named set to the value given, held to its row, in the file that is its home; or
    refused with nothing written and the value never said. Returns the exit status."""
    try:
        read, layers = _start(host)
        row, why, where = settable(name)
        if row is None:
            history = HISTORY if where in (VAULT, FILE) else ""
            raise _Refused(_why_not(read, name, why, where) + history)
        file, digest = _file(read, layers, row)
        typed = _typed(read, row, value, _hunted(read))
        after = overrides.set(read.home, name, typed, read.host, digest, environ=read.environ)
    except REFUSALS as refused:
        return _refusal(refused)
    wrote = after != digest
    flag = read.host if read.host and read.many else ""
    line = f"{name}: {json_text.dumps(typed, ensure_ascii=False)}"
    result = {"asked": name, "name": name, "value": typed, "wrote": line if wrote else None, "file": file}
    notes = []
    if not wrote:
        notes.append(ALREADY.format(file=file))
    else:
        if host and row.home == overrides.FLEET:
            notes.append(FLEET_WIDE.format(name=name))
        notes += applies(name, flag)
    head = text.one_line(f"wrote {line} in {file} ({WHOLE})", 1000) if wrote else ""
    now = Read(read.home, read.host, read.many, read.environ).one(name)
    return _told("wrote", result, now, notes, head, json is True)


def reset(run: Any, *, name: str, host: str | None = None, json: bool = False) -> int:
    """--reset: the one setting named taken out of the file that is its home, so that its default applies again; or
    refused with nothing written, as write is. Returns the exit status."""
    try:
        read, layers = _start(host)
        row, why, where = settable(name)
        if row is None:
            raise _Refused(_why_not(read, name, why, where))
        file, digest = _file(read, layers, row)
        after = overrides.unset(read.home, name, read.host, digest, environ=read.environ)
    except REFUSALS as refused:
        return _refusal(refused)
    removed = after != digest
    now = Read(read.home, read.host, read.many, read.environ).one(name)
    result = {"asked": name, "name": name, "removed": [name] if removed else [], "file": file}
    if not removed:
        notes = [AT_DEFAULT.format(file=file, name=name)]
    else:
        notes = [NOW.format(name=name, value=shown(now), source=source(now)[0])] if now else []
        notes += applies(name, read.host if read.host and read.many else "")
    head = f"reset {name} in {file}: its line taken out ({WHOLE})" if removed else ""
    return _told("reset", result, now, notes, head, json is True)


# ----------------------------------------------------------------------------------------------- edit ---


def _text(value: object) -> str:
    """A value as --value takes it, so that the text typed back is the value again: a list or a mapping as JSON, which
    YAML's flow reads."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return json_text.dumps(registry.plain(value), ensure_ascii=False)


def _entries(read: Read) -> list[tuple[registry.Row, str | None, str]]:
    """Every setting edit lists, the table's rows and then the options passed through, each that settable takes and
    that one line holds: its row, the text of the value its file sets (None where none sets it) and of its value."""
    rows = [*registry.rows(), *(passthrough.row(each.name) for each in passthrough.options() if each.refused is None)]
    found = []
    for each in rows:
        row, _why, _where = settable(each.name)
        if row is None or row.type == "text":
            continue
        value = read.values.get(row.name)
        held = None if value is None else _text(value.value)
        found.append((row, held if value is not None and value.layer == row.home else None, held or ""))
    return found


def _document(read: Read, entries: Sequence[tuple[registry.Row, str | None, str]]) -> str:
    """The settings as one document: its head, then each setting under its description, set or put # before."""
    lines = [f"# {piece}" for piece in EDIT_HEAD]
    lines[0] = lines[0].format(host=f"the host {read.host}" if read.host else "a fleet with no host yet")
    for row, set_to, value in entries:
        lines.append("")
        lines += [f"# {piece}" for piece in screen.fill(text.one_line(row.description, 4000), WIDTH - 2)]
        lines.append(f"{row.name}: {set_to}" if set_to is not None else f"# {row.name}: {value}")
    return "\n".join(lines) + "\n"


def _changes(
    document: str, entries: Sequence[tuple[registry.Row, str | None, str]], read: Read, held: Sequence[Secret]
) -> tuple[list[tuple[registry.Row, object]], list[str]]:
    """What the document changes: each line whose value differs from the one the file set, typed, and each setting set
    before whose line is gone, as None; and each line refused, by its number, never saying the value back."""
    listed = {row.name: (row, set_to) for row, set_to, _value in entries}
    seen: dict[str, int] = {}
    changes: list[tuple[registry.Row, object]] = []
    refusals: list[str] = []
    for number, line in enumerate(document.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, colon, given = (part.strip() for part in stripped.partition(":"))
        if not colon or not patterns.full("setting_name", name):
            refusals.append(f"line {number}: {NOT_A_LINE}")
            continue
        if name not in listed:
            row, why, where = settable(name)
            said_why = _why_not(read, name, why, where) if row is None else f"{name} {overrides.TEXT_TYPED}"
            refusals.append(f"line {number}: {said_why.removeprefix('error: ')}")
            continue
        if name in seen:
            refusals.append(f"line {number}: {TWICE.format(name=name, first=seen[name])}")
            continue
        seen[name] = number
        row, set_to = listed[name]
        if set_to is not None and set_to.strip() == given:
            continue
        try:
            changes.append((row, _typed(read, row, given, held)))
        except (overrides.Refused, _Refused) as refused:
            refusals.append(f"line {number}: {refused}")
    changes += [(row, None) for row, set_to in listed.values() if set_to is not None and row.name not in seen]
    return changes, refusals


def _edited(path: Path, command: list[str]) -> str:
    """The document as the editor left it, once it ends."""
    import subprocess

    try:
        ended = subprocess.run([*command, str(path)], check=False).returncode
    except OSError as error:
        raise _Refused(NO_EDITOR.format(why=error.strerror or error)) from None
    if ended != 0:
        raise _Refused(EDITOR_FAILED.format(code=ended))
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise _Refused(NOT_TEXT) from None


def edit(run: Any, *, host: str | None = None) -> int:
    """edit: every setting the writes take in the editor (VISUAL, EDITOR or vi), and on its return each changed line
    written as --value or --reset writes it, all of them once none is refused; a line refused is said by its number
    and the document offered again. Refused with no terminal. Returns the exit status."""
    import shlex
    import tempfile

    if not sys.stdin.isatty():
        return _refused(said(NO_TERMINAL, {"edit": _EDIT}))
    command = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "") or ["vi"]
    lines: list[list] = []
    try:
        read, layers = _start(host)
        entries = _entries(read)
        held = _hunted(read)
        with tempfile.TemporaryDirectory(prefix="aw-settings-") as folder:
            path = Path(folder) / "settings.yml"
            path.write_text(_document(read, entries), encoding="utf-8")
            while True:
                changes, refusals = _changes(_edited(path, command), entries, read, held)
                if not refusals:
                    break
                for each in refusals:
                    print(f"error: {each}", file=sys.stderr)
                print(AGAIN, end="", file=sys.stderr, flush=True)
                answer = sys.stdin.readline()
                if not answer or answer.strip().lower() in ("n", "no"):
                    raise _Refused("nothing was written")
        if not changes:
            _print([[screen.seg(UNCHANGED, "d")]])
            return EXITS["done"]
        digests: dict[str, str] = {}
        for row, value in changes:
            file, digest = _file(read, layers, row)
            digest = digests.get(file, digest)
            if value is None:
                digests[file] = overrides.unset(read.home, row.name, read.host, digest, environ=read.environ)
                lines.append([screen.seg(f"reset {row.name} in {file}", "y")])
            else:
                digests[file] = overrides.set(read.home, row.name, value, read.host, digest, environ=read.environ)
                written = text.one_line(f"wrote {row.name}: {json_text.dumps(value, ensure_ascii=False)}", 1000)
                lines.append([screen.seg(f"{written} in {file}", "y")])
    except REFUSALS as refused:
        # What was written before a write was refused stands: said first.
        _print(lines)
        return _refusal(refused)
    notes = list(dict.fromkeys(note for row, _value in changes for note in applies(row.name, "")))
    lines.append([screen.seg(f"  {WHOLE}")])
    lines += [[screen.seg(f"  {piece}")] for note in notes for piece in screen.fill(note, WIDTH - 2)]
    _print(lines)
    return EXITS["done"]


def settings(run: Any, **given: Any) -> int:
    """aw settings's handler, as words.py's row names it: with --value `write`, with --reset `reset`, with the word
    edit `edit`, else a read, `show`; each takes the options it reads, by the names the parser gives them."""
    value = given.pop("value", None)
    resetting = given.pop("reset", False) is True
    taken = {key: given[key] for key in ("name", "host", "json") if key in given}
    if value is not None:
        return write(run, value=value, **taken)
    if resetting:
        return reset(run, **taken)
    if given.get("name") == EDIT:
        if given.get("changed") is True or given.get("json") is True or given.get("all") is True:
            return _refused(said(EDIT_ALONE, {"edit": _EDIT}))
        return edit(run, host=given.get("host"))
    return show(run, **given)
