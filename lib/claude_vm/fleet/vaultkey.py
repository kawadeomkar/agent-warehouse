"""The vault's key: an age key in a file of this computer's own, found in one order (docs/architecture/engine.md
§16.9).

The key that opens the vault (fleet/sealed.py) is a random age key, `AGE-SECRET-KEY-1...`, one line in a file that
holds it and nothing else. By default that file is `paths.key_file`: `vault-key` at the top of a checkout's `.aw/`,
beside `client.yml`, or in the data folder for the installed program. It is never in the fleet's folder, which a person
copies to another computer and may publish, nor in the state folder, which can be made again and which a cleanup may
remove, nor, for the installed program, in its config folder, which people publish: a file named in one of them is
refused by name (`Misplaced`). What a person backs up is the fleet's folder and, apart from it, the key.

`make_key(path)` makes a key with `pyrage` and writes it to path, `O_EXCL` at 0600, only where nothing is there, and
never writes over one; it returns the key. Nothing here removes, moves or rewrites a key file.

**One order.** `source(where, ...)` returns the first place that exists, from the values its caller gives it (the
caller reads `vault_key_file` and `vault_key_command` through `overrides.resolve`), so this module reads no file of
settings:

1. `AGENT_WAREHOUSE_VAULT_KEY_FILE`, the one override, for a script and for the agents' server: the path of a file
   that holds the key and nothing else, read and never run, whatever its mode.
2. `vault_key_command`, a command of the person's whose output is the key. Its place in the order is here; running it
   is not built yet, so a run where it is set refuses, saying so, and never reads the file after it.
3. The file `vault_key_file` names, `""` the default above.

`ANSIBLE_VAULT_PASSWORD_FILE`, which opens today's ansible-vault file, is never read and is no refusal.

**A new fleet's vault is sealed to its own key file's key.** Where the fleet has no vault yet, a key is given back only
from the key file (`place`), which `aw init` makes: the variable's file and a command are refused there, in one
sentence (`NoVaultYet`), and nothing is read or run, so that the vault `init` makes is never sealed, with no word, to a
key named by a leftover export, a script's environment, or a project's settings, which reach every command Claude Code
runs there.

**One sentence when the vault cannot be opened.** `open(where, ...)` returns the key, a `Secret`, once the fleet's vault
opens with it (where there is no vault yet, once the key file holds an age key), or raises one of three sentences:
nothing here can open the vault, with what would, the key file `vault_key_file` names (`NoKey`); the place that exists
failed, in its own words made one line (`Failed`); the place answered and what it gave does not open this vault
(`WrongKey`). A place that exists and fails is a refusal: the next place is never tried, and nothing is tried again. The
vault's own refusals (no `fleet.json`, a newer format, a file that is no vault) are sealed.py's, raised as they are.
Every refusal is a `sealed.Refused`, so a caller catches the vault's and the key's as one, and exits 1, or 3 from
`status`. No refusal says the key, a value given where a path goes, or anything a file held. A run with nobody at the
terminal opens the vault as one at a terminal does: nothing here reads the terminal or asks.

Standard library only at import, with fleet/ and the printer; `pyrage` is imported inside the functions that need it.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from claude_vm.fleet import files, paths, sealed
from claude_vm.schema.printer import said
from claude_vm.schema.words import PLACEHOLDER, Line
from claude_vm.secret import Secret
from claude_vm.text import one_line, plain_text

# The one override: the path of a file that holds the key, for a script and for the agents' server, which `aw mcp
# --vault-key-file` records at registration and hands each run as this variable.
VARIABLE = "AGENT_WAREHOUSE_VAULT_KEY_FILE"
# The places, in their order: the variable's file, the person's command, the key file.
OVERRIDE, COMMAND, FILE = "variable", "command", "file"
# The most of a key's file that is read: an age key is one line of 74 characters.
MOST = 1024


# The key cannot be found, read or used: one sentence, never the key. One class with the vault's own refusals.
Refused = sealed.Refused


class Misplaced(Refused):
    """A key file named inside a folder the key is never kept in: nothing is read there."""


class NoKey(Refused):
    """No place exists that could give the key: the sentence says what would."""


class Failed(Refused):
    """The place that exists could not give a key: said in its own words, made one line. Nothing else is tried."""


class WrongKey(Refused):
    """The place answered, and what it gave does not open this vault. Nothing else is tried."""


class NoVaultYet(Refused):
    """The fleet has no vault yet, and the place that exists is the variable's file or a command, not the key file: a
    new fleet's vault is sealed to the key in its own key file alone, so nothing is read or run."""


@dataclass(frozen=True)
class Source:
    """The first place that exists: `place` is OVERRIDE, COMMAND or FILE; `path` the file read, for OVERRIDE and FILE;
    `words` the command, for COMMAND; `default` whether the file is the home's own key file, not one a person named."""

    place: str
    path: Path | None = None
    words: tuple[str, ...] = ()
    default: bool = False

    def said(self) -> str:
        """The place as a sentence names it: the file by its path, the command never by its words."""
        if self.place == COMMAND:
            return "the command vault_key_command names"
        shown = _shown(self.path)
        if self.place == OVERRIDE:
            return f"{shown}, the file {VARIABLE} names"
        return f"the key file {shown}" if self.default else f"{shown}, the file vault_key_file names"


def _shown(path: str | os.PathLike | None) -> str:
    return plain_text(str(path))


def _words(error: OSError) -> str:
    """What the system said of a failure, made one line: its own words, never the path, which the sentence names."""
    return one_line(error.strerror or type(error).__name__)


def _absolute(given: str | os.PathLike, environ: Mapping[str, str], named: str) -> Path:
    """given as an absolute path, ~ or ~/ at its start the user's home. A value still relative is refused naming
    named, and never said: a key pasted where its file's path goes would be on the screen."""
    text = os.fspath(given)
    if not isinstance(text, str):
        raise TypeError(f"{named} is a path, not {type(text).__name__}")
    if text == "~" or text.startswith("~/"):
        try:
            text = str(paths.user_home(environ)) + text[1:]
        except paths.Refused as error:
            raise Failed(f"error: {named} starts with ~, and the home it names is not known here ({error})") from None
    if not os.path.isabs(text):
        raise Failed(
            f"error: {named} is not an absolute path: name the file that holds the vault's key from / or from ~,"
            " and the value given is not said"
        )
    return Path(os.path.normpath(text))


def _apart(where: paths.Home) -> list[tuple[Path, str]]:
    """The folders a key file is never in, each with why: the fleet's folder, the state folder, and the home itself
    where its own key is kept outside it (the installed program's config folder)."""
    folders = [
        (paths.fleet_dir(where), "the fleet's folder, which is copied to another computer"),
        (paths.state(where), "the state folder, which a cleanup may remove"),
    ]
    if not paths.inside(paths.key_file(where), where.root):
        folders.append((where.root, "the installed program's config folder, which people publish"))
    return folders


def _held(where: paths.Home, path: Path, named: str) -> Path:
    """path, held to lie in none of the folders a key file is never in; Misplaced, by name, where it does. Decided by
    paths.inside, so a link or a disk that ignores case spells no folder past it."""
    for folder, why in _apart(where):
        if paths.inside(path, folder):
            raise Misplaced(
                f"error: {named} is {_shown(path)}, inside {_shown(folder)}, {why}: the vault's key is kept apart from"
                " it, so nothing is read there; name a file outside it"
            )
    return path


def place(where: paths.Home, key_file: str | os.PathLike | None, *, environ: Mapping[str, str] | None = None) -> Path:
    """The file vault_key_file names for the fleet at where: its value as the caller resolved it, `""` (or None) the
    home's own key file, `paths.key_file`; held to lie in no folder a key file is never in (Misplaced)."""
    env = os.environ if environ is None else environ
    if key_file is None or os.fspath(key_file) == "":
        return paths.key_file(where)
    return _held(where, _absolute(key_file, env, "vault_key_file"), "vault_key_file")


def source(
    where: paths.Home,
    *,
    key_file: str | os.PathLike | None,
    key_command: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> Source | None:
    """The first place that exists, in the one order: the variable's file where the variable is set, the command where
    vault_key_command names one, the file vault_key_file names where anything is there; None where none does. A place
    named in a folder the key is never kept in is refused (Misplaced), and a value that is no path (Failed)."""
    env = os.environ if environ is None else environ
    given = env.get(VARIABLE, "")
    if given:
        return Source(OVERRIDE, _held(where, _absolute(given, env, VARIABLE), VARIABLE))
    if isinstance(key_command, str) or not all(isinstance(word, str) for word in key_command):
        raise TypeError("vault_key_command is a list of words")
    if key_command:
        return Source(COMMAND, words=tuple(key_command))
    found = place(where, key_file, environ=env)
    if not os.path.lexists(found):
        return None
    return Source(FILE, found, default=found == paths.key_file(where))


def _read(found: Source) -> bytes:
    """What the file of found holds, at most MOST and a byte: Failed, in the system's words, where it cannot be read or
    is no file. Opened to read and never run, whatever its mode, and without waiting on a pipe."""
    assert found.path is not None
    try:
        handle = os.open(found.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise Failed(f"error: {found.said()} cannot be read ({_words(error)}): nothing else is tried") from None
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise Failed(f"error: {found.said()} is not a file: nothing else is tried")
        data = b""
        while len(data) <= MOST:
            more = os.read(handle, MOST + 1 - len(data))
            if not more:
                break
            data += more
        return data
    except OSError as error:
        raise Failed(f"error: {found.said()} cannot be read ({_words(error)}): nothing else is tried") from None
    finally:
        os.close(handle)


def _is_age_key(text: str) -> bool:
    import pyrage
    from pyrage import x25519

    try:
        x25519.Identity.from_str(text)
    except pyrage.IdentityError:
        return False
    return True


def _key(found: Source, data: bytes) -> Secret:
    """The key data holds, white space at its ends aside; WrongKey, never saying what data holds, where it is none."""
    try:
        text = data.decode("ascii").strip(" \t\r\n") if len(data) <= MOST else ""
    except UnicodeDecodeError:
        text = ""
    if not text or not _is_age_key(text):
        raise WrongKey(
            f"error: {found.said()} holds no age key, so it does not open the fleet's vault: the file holds the key"
            " alone, one line that starts AGE-SECRET-KEY-1; nothing else is tried"
        )
    return Secret(text)


def _nothing(where: paths.Home, file: Path) -> str:
    """The first sentence: no place exists, and what would open the vault, by the key file vault_key_file names and
    never the variable, which is a script's and the agents' server's."""
    vault = sealed.path(where)
    if os.path.lexists(vault):
        return f"error: nothing here opens {_shown(vault)}: " + said(
            "the key file {file} (vault_key_file) is not there, and vault_key_command names no command: copy the key"
            " file of the computer that made the fleet to {file}, or name the file that holds it in vault_key_file:"
            " {settings}",
            {
                "file": _shown(file),
                "settings": Line("settings", {"name": "vault_key_file", "value": PLACEHOLDER}),
            },
        )
    return "error: nothing here opens the fleet's vault: " + said(
        "it has none yet, and the key file {file} (vault_key_file) is not there: {init} makes the fleet, its key there"
        " and its vault",
        {"file": _shown(file), "init": Line("init")},
    )


def _unmade(found: Source, file: Path) -> str:
    """The sentence for a fleet with no vault yet where the place that exists is the variable's file or a command."""
    if found.place == COMMAND:
        named, undo, nothing = "vault_key_command names a command", "take it out of client.yml", "nothing is run"
    else:
        named, undo, nothing = f"{VARIABLE} names {_shown(found.path)}", f"unset {VARIABLE}", "nothing is read"
    return f"error: {named}, and the fleet has no vault yet: " + said(
        "a new fleet's vault is sealed to the key in its own key file, {file} (vault_key_file), which {init} makes,"
        " and never to one named elsewhere: {undo} for {init}; {nothing}",
        {"file": _shown(file), "init": Line("init"), "undo": undo, "nothing": nothing},
    )


def open(
    where: paths.Home,
    *,
    key_file: str | os.PathLike | None,
    key_command: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> Secret:
    """The vault's key, from the first place that exists (`source`), once the fleet's vault at where opens with it, or
    with no vault yet once it is an age key from the key file. Raises NoKey, Failed or WrongKey, one sentence each,
    Misplaced, or NoVaultYet where there is no vault yet and the place is the variable's file or a command; the vault's
    own refusals are sealed.py's, raised as they are."""
    env = os.environ if environ is None else environ
    found = source(where, key_file=key_file, key_command=key_command, environ=env)
    if found is None:
        raise NoKey(_nothing(where, place(where, key_file, environ=env)))
    if found.place != FILE and not os.path.lexists(sealed.path(where)):
        raise NoVaultYet(_unmade(found, place(where, key_file, environ=env)))
    if found.place == COMMAND:
        raise Failed(
            "error: vault_key_command names a command for the vault's key, and this version of the program runs none"
            " yet: take it out of client.yml, and the key file is read; nothing else is tried"
        )
    key = _key(found, _read(found))
    try:
        opened = sealed.read(where, key)
    except sealed.NotOpened:
        raise WrongKey(
            f"error: the key in {found.said()} does not open {_shown(sealed.path(where))}: it is another fleet's key,"
            " or the vault was changed after it was written; nothing else is tried"
        ) from None
    if opened is None and found.place != FILE:
        # The vault went between the look above and the read: a key from elsewhere than the key file is given back
        # only for a vault it opened.
        raise NoVaultYet(_unmade(found, place(where, key_file, environ=env)))
    return key


def make_key(path: str | os.PathLike) -> Secret:
    """A new age key, written to path and returned: `O_EXCL`, never through a link, at 0600 whatever the umask, and only
    where nothing is there, so a key is never written over. Its folder is made at 0700 through files.folder where it is
    missing; one that is there is the person's, taken as it is. Refused, with nothing written over, where anything is
    there or the file cannot be made; a write cut short leaves what it wrote, said, and never removed here."""
    from pyrage import x25519

    where = Path(os.path.abspath(path))
    if not os.path.isdir(where.parent):
        try:
            files.folder(where.parent)
        except files.Unsafe as error:
            raise Refused(str(error)) from None
    shown = _shown(where)
    try:
        handle = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, paths.FILE_MODE)
    except FileExistsError:
        raise Refused(f"error: {shown} is there already: a key is made once, and nothing is written over it") from None
    except OSError as error:
        raise Refused(f"error: {shown} cannot be made ({_words(error)}): no key was made") from None
    text = str(x25519.Identity.generate())
    try:
        os.fchmod(handle, paths.FILE_MODE)
        data = (text + "\n").encode("ascii")
        while data:
            data = data[os.write(handle, data) :]
        os.fsync(handle)
    except OSError as error:
        raise Refused(
            f"error: {shown} could not be written whole ({_words(error)}): what it holds is no key, and it is left as"
            " it is; remove it, and make the key again"
        ) from None
    finally:
        os.close(handle)
    _synced(where.parent)
    return Secret(text)


def _synced(folder: Path) -> None:
    """The new key's name made to last in its folder, where the system can say so: a folder that cannot be flushed is
    left to the system, since the key is written and flushed already."""
    try:
        handle = os.open(folder, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)
