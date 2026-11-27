"""aw settings's reads: each setting's value and the layer that set it, for one group, one name, every setting or what
the files change, from the settings table (fleet/registry.py) under the overrides (fleet/overrides.py), with no
Ansible (docs/architecture/engine.md §16.9).

`show` is the handler words.py's row of settings names. Its forms, each on screen or, with --json, as one document
alone on standard output with today's keys:

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
opens it. No read writes a file or starts a child. The writes, --value, --reset and edit, are card D10's: refused here,
naming it.

The line builders are today's tools/settings' (`shown`, `source`, `table`, `tail`, `one`), less everything that asked
Ansible. Standard library, the table and the overrides at import; the YAML reader through the overrides, and pyrage
through the vault's key, for --all alone.
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

from claude_vm import text
from claude_vm.fleet import hosts, overrides, passthrough, paths, registry, secrets
from claude_vm.hosts import model
from claude_vm.schema import printer
from claude_vm.schema.printer import said
from claude_vm.schema.words import EXITS, PLACEHOLDER, Line
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

# The lines a sentence here names.
_SET = Line("settings", {"name": PLACEHOLDER, "value": PLACEHOLDER})
_VAULT = Line("vault", {"name": PLACEHOLDER})
_ONE = Line("settings", {"name": PLACEHOLDER})
_ALL = Line("settings", {"all": True})
_CHANGED = Line("settings", {"changed": True})
_JSON = Line("settings", {"name": PLACEHOLDER, "json": True})
_RESET = Line("settings", {"name": PLACEHOLDER, "reset": True})

# The sentences, each said through the printer of a command.
WRITES = "error: {set}, --reset and edit write a setting, which card D10 builds: nothing was written"
UNKNOWN = "error: that is neither a group ({groups}) nor a setting or a role's option{close}"
TAIL = "Change a setting with {set}, a secret with {vault}."
USAGE = (
    "Each setting's value and where it was set, for one group, one setting or all of them.",
    "Reads without changing anything. A secret is never shown: it is set or not set.",
)
USAGE_LINES = (
    ("  {form}  every setting of one group, or one setting explained", {"form": _ONE}),
    ("  {form}  every setting, and which secrets of the vault are set", {"form": _ALL}),
    ("  {form}  only what your files change: alone, all of it", {"form": _CHANGED}),
    ("  {form}  the same, as JSON on standard output", {"form": _JSON}),
    ("  {form}  set one, and {reset} puts one back (card D10)", {"form": _SET, "reset": _RESET}),
)
VAULT_UNREAD = "the vault was not opened, so whether each of its secrets is set is not known here: "


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
    reset: bool = False,
    value: str | None = None,
) -> int:
    """aw settings's reads, each form its documented output (the module's docstring); the writes refused, naming the
    card that builds them. Returns the exit status."""
    if value is not None or reset or name == "edit":
        return _refused(said(WRITES, {"set": _SET}))
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
