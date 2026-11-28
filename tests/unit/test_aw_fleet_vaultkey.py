"""The vault's key: a file of this computer's own, found in one order (lib/claude_vm/fleet/vaultkey.py;
docs/architecture/engine.md §16.9).

The key is made once, `O_EXCL` at 0600, at paths.key_file by default, and never written over. A run finds it in one
order: AGENT_WAREHOUSE_VAULT_KEY_FILE, the one override, which names a file read and never run, even where it may be
run; then vault_key_command, whose running is not built yet and refuses; then the file vault_key_file names, "" the
default. A key file open to the group or others, to read, write or run (ssh's 077), is refused, whichever place it
is, as ssh refuses such a key, before anything of it is read, in one sentence that says chmod 600 <file>. A place
that exists and fails is a refusal and the next is never tried; with no place, the first sentence says what would open
the vault, by vault_key_file and never the variable; a place that answers with what does not open the vault is the
third. With no vault yet a key comes only from the key file: the variable's file and a command are refused there in
one sentence, nothing read or run, and the key file left as it is. ANSIBLE_VAULT_PASSWORD_FILE is never read. A key
file named in the fleet's folder, the state folder or the installed program's config folder is refused by name. A vault
made by sealed.py under a key made here opens in a child with no terminal and standard input from /dev/null.

The invariant with its own test: only vaultkey.py names the variable, and only paths.py and vaultkey.py the key file's
name, so a run reads the key only through vaultkey.open; and no sentence holds the key or anything a file held: every
key and fake secret a case uses is looked for in its refusals and in everything it and its children printed. The public
half of every key a case makes, as good as the key for writing a vault, is looked for there too, and in every file under
the case's own directory, its home among them.
"""

from __future__ import annotations

import ast
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from aw_kit import environment, imports_of
from harness import REPO
from pyrage import x25519

from claude_vm.fleet import paths, record, sealed, vaultkey
from claude_vm.schema.printer import quoted, said
from claude_vm.schema.words import PLACEHOLDER, Line
from claude_vm.secret import Secret

LIB = REPO / "lib"
FLEET = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
VARIABLE = vaultkey.VARIABLE
# Made-up secrets of low entropy, which no sentence or output may hold.
WORD = "made-up-token-0000"
NOT_A_KEY = "made-up-not-a-key-0000"
COMMAND_WORD = "made-up-command-word-0000"
FAKES = (WORD, NOT_A_KEY, COMMAND_WORD)
# The new engine's subpackages, where ANSIBLE_VAULT_PASSWORD_FILE is never named (tests/unit/test_aw_layout.py's NEW).
NEW = ("schema", "cli", "engine", "pve", "ssh", "vendors", "runs", "fleet", "guest", "control")


def _public(key: Secret) -> str:
    """key's public half as age writes one, age1...: what a vault is sealed to, never written or said."""
    return str(x25519.Identity.from_str(key.reveal()).to_public())


def _publics_nowhere(root: Path, used: list[str]) -> None:
    """No file under root holds the public half of a key a case made. The key itself is in its key file by design."""
    publics = [value for value in used if value.startswith("age1")]
    for folder, _dirs, names in os.walk(root):
        for name in names:
            found = Path(folder) / name
            if found.is_symlink() or not found.is_file():
                continue
            if not os.access(found, os.R_OK):
                found.chmod(0o600)
            data = found.read_bytes()
            assert not [value for value in publics if value.encode() in data], f"{found} holds a key's public half"


@pytest.fixture(autouse=True)
def seen(tmp_path: Path, capfd) -> Iterator[list[str]]:
    """Every key, its public half and every fake secret a case uses: none in anything it or its children printed, and no
    public half in a file under its own directory, once it ends."""
    used = list(FAKES)
    yield used
    _publics_nowhere(tmp_path, used)
    out, err = capfd.readouterr()
    assert not [value for value in used if value in out or value in err], "a secret was printed"


def _saw(seen: list[str], key: Secret) -> Secret:
    """key, and its public half, among what nothing may say: key as given back."""
    seen.extend((key.reveal(), _public(key)))
    return key


def _key(seen: list[str]) -> Secret:
    return _saw(seen, Secret(str(x25519.Identity.generate())))


def _refused(error: pytest.ExceptionInfo, seen: list[str]) -> str:
    """The refusal's sentence, held to say no key and no secret."""
    sentence = str(error.value)
    assert not [value for value in seen if value in sentence], "a refusal said a secret"
    assert "\n" not in sentence and sentence.startswith("error: "), sentence
    return sentence


def _where() -> paths.Home:
    """The test's own fleet home (AGENT_WAREHOUSE_HOME, set by conftest.py's aw_home)."""
    return paths.home(REPO)


def _env(home: Path, **more: str) -> dict[str, str]:
    """A run's environment for vaultkey: the person's home, and no override unless a case gives one."""
    return {"HOME": str(home), **more}


def _put(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def _open(where: paths.Home, home: Path, key_file: str | None = "", key_command=(), **more: str) -> Secret:
    return vaultkey.open(where, key_file=key_file, key_command=key_command, environ=_env(home, **more))


@pytest.fixture
def fleet(home: Path) -> paths.Home:
    """A fleet's record, which a vault is kept beside."""
    where = _where()
    record.write(where, record.first(FLEET))
    return where


@pytest.fixture
def made(fleet: paths.Home, seen: list[str]) -> Secret:
    """The home's own key file made here, and the fleet's vault sealed to it, with one secret in it."""
    key = vaultkey.make_key(paths.key_file(fleet))
    _saw(seen, key)
    sealed.create(fleet, key)
    sealed.put(fleet, key, "github_token", Secret(WORD))
    return key


# ---------------------------------------------------------------- the key made ----


def test_a_key_is_made_once_at_the_home_s_key_file_and_never_over_one(home: Path, seen: list[str]) -> None:
    where = _where()
    path = paths.key_file(where)
    assert path == home / "vault-key"
    key = vaultkey.make_key(path)
    _saw(seen, key)
    assert isinstance(key, Secret) and repr(key) == "Secret('********')"
    assert path.read_text() == key.reveal() + "\n"
    assert x25519.Identity.from_str(key.reveal())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    with pytest.raises(vaultkey.Refused) as refused:
        vaultkey.make_key(path)
    assert (
        _refused(refused, seen) == f"error: {path} is there already: a key is made once, and nothing is written over it"
    )
    assert path.read_bytes() == before
    # Two keys made are two keys.
    other = vaultkey.make_key(home / "other-key")
    _saw(seen, other)
    assert other.reveal() != key.reveal()


def test_a_key_s_folder_is_made_at_0700_and_its_file_at_0600_whatever_the_umask(tmp_path: Path, seen) -> None:
    path = tmp_path / "keys" / "deeper" / "vault-key"
    was = os.umask(0o277)
    try:
        key = vaultkey.make_key(path)
    finally:
        os.umask(was)
    _saw(seen, key)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [stat.S_IMODE((tmp_path / name).stat().st_mode) for name in ("keys", "keys/deeper")] == [0o700, 0o700]


def test_a_key_is_never_written_through_a_link_and_a_folder_there_is_taken_as_it_is(tmp_path: Path, seen) -> None:
    # A link where the key goes, to a file or to none, is something there: nothing is written through it.
    target = tmp_path / "target"
    for link in (tmp_path / "dangling", tmp_path / "to-a-file"):
        if link.name == "to-a-file":
            _put(target, "the person's own\n")
        link.symlink_to(target)
        with pytest.raises(vaultkey.Refused, match="is there already"):
            vaultkey.make_key(link)
    assert target.read_text() == "the person's own\n"
    # A folder that is there is the person's, a link to one among them (an encrypted volume, say).
    (tmp_path / "volume").mkdir()
    (tmp_path / "keys").symlink_to(tmp_path / "volume")
    key = vaultkey.make_key(tmp_path / "keys" / "vault-key")
    _saw(seen, key)
    assert (tmp_path / "volume" / "vault-key").read_text() == key.reveal() + "\n"


def test_a_key_cannot_be_made_where_a_file_stands_for_its_folder(tmp_path: Path, seen) -> None:
    _put(tmp_path / "file", "x\n")
    with pytest.raises(vaultkey.Refused) as refused:
        vaultkey.make_key(tmp_path / "file" / "vault-key")
    assert "file" in _refused(refused, seen)


# ---------------------------------------------------------------- the order ----


def test_the_home_s_key_file_opens_the_vault_it_was_made_for(home: Path, fleet, made: Secret, seen) -> None:
    for key_file in ("", None, str(paths.key_file(fleet))):
        found = vaultkey.source(fleet, key_file=key_file, key_command=(), environ=_env(home))
        assert found == vaultkey.Source(vaultkey.FILE, paths.key_file(fleet), default=True)
        key = _open(fleet, home, key_file)
        assert key.reveal() == made.reveal()
    assert sealed.read(fleet, key)["github_token"].reveal() == WORD


def test_each_place_in_its_order_the_earlier_winning(home: Path, tmp_path: Path, fleet, made: Secret, seen) -> None:
    other = _key(seen)
    given = _put(tmp_path / "given-key", other.reveal() + "\n")
    named = _put(tmp_path / "named-key", made.reveal() + "\n")
    order = {
        "variable over the command and the file": (
            {VARIABLE: str(given)},
            [COMMAND_WORD],
            vaultkey.Source(vaultkey.OVERRIDE, given),
        ),
        "the command over the file": (
            {},
            [COMMAND_WORD, "--flag"],
            vaultkey.Source(vaultkey.COMMAND, words=(COMMAND_WORD, "--flag")),
        ),
        "the file vault_key_file names": ({}, [], vaultkey.Source(vaultkey.FILE, named)),
        "the home's key file": ({}, [], vaultkey.Source(vaultkey.FILE, paths.key_file(fleet), default=True)),
    }
    for case, (more, command, expected) in order.items():
        key_file = str(named) if case == "the file vault_key_file names" else ""
        found = vaultkey.source(fleet, key_file=key_file, key_command=command, environ=_env(home, **more))
        assert found == expected, case
    # The variable's key is the one used, and it is another fleet's here: the file after it is never tried.
    with pytest.raises(vaultkey.WrongKey) as refused:
        _open(fleet, home, str(named), **{VARIABLE: str(given)})
    assert _refused(refused, seen) == (
        f"error: the key in {given}, the file {VARIABLE} names does not open {sealed.path(fleet)}: it is another"
        " fleet's key, or the vault was changed after it was written; nothing else is tried"
    )
    # Unset, the file vault_key_file names is read; set, the variable's file is read over a file that would not open
    # the vault.
    assert _open(fleet, home, str(named)).reveal() == made.reveal()
    assert _open(fleet, home, str(given), **{VARIABLE: str(named)}).reveal() == made.reveal()


def test_a_command_named_is_its_place_and_is_never_run_yet(home: Path, tmp_path: Path, fleet, made, seen) -> None:
    # Its running is D13's: until then a command set refuses, and the key file after it, which would open the vault,
    # is never read in its place. Nothing is run: the program named would leave its mark.
    mark = tmp_path / "ran"
    program = _put(tmp_path / "program", f"#!/bin/sh\n: > {mark}\n", mode=0o755)
    with pytest.raises(vaultkey.Failed) as refused:
        _open(fleet, home, "", key_command=[str(program), COMMAND_WORD])
    sentence = _refused(refused, seen)
    assert sentence == (
        "error: vault_key_command names a command for the vault's key, and this version of the program runs none"
        " yet: take it out of client.yml, and the key file is read; nothing else is tried"
    )
    assert str(program) not in sentence and not mark.exists()
    with pytest.raises(TypeError, match="a list of words"):
        _open(fleet, home, "", key_command=str(program))


# ---------------------------------------------------------------- a place that exists and fails ----


def _fifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def test_a_place_that_exists_and_fails_is_a_refusal_and_the_next_is_not_tried(
    home: Path, tmp_path: Path, fleet, made, seen
) -> None:
    # The key file after each would open the vault: none of them is ever tried in its place.
    folder = tmp_path / "a-folder"
    folder.mkdir()
    failing = {
        tmp_path / "missing": "cannot be read (No such file or directory)",
        folder: "is not a file",
        _fifo(tmp_path / "a-pipe"): "is not a file",
    }
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    failing[tmp_path / "dangling"] = "cannot be read (No such file or directory)"
    if os.geteuid() != 0:
        failing[_put(tmp_path / "unreadable", made.reveal() + "\n", mode=0o000)] = "cannot be read (Permission denied)"
    for path, why in failing.items():
        with pytest.raises(vaultkey.Failed) as refused:
            _open(fleet, home, "", **{VARIABLE: str(path)})
        assert _refused(refused, seen) == f"error: {path}, the file {VARIABLE} names {why}: nothing else is tried"
    # The same for the file vault_key_file names, and for the home's own.
    with pytest.raises(vaultkey.Failed) as refused:
        _open(fleet, home, str(folder))
    assert (
        _refused(refused, seen)
        == f"error: {folder}, the file vault_key_file names is not a file: nothing else is tried"
    )
    paths.key_file(fleet).chmod(0o000)
    if os.geteuid() != 0:
        with pytest.raises(vaultkey.Failed) as refused:
            _open(fleet, home, "")
        assert _refused(refused, seen) == (
            f"error: the key file {paths.key_file(fleet)} cannot be read (Permission denied): nothing else is tried"
        )


def test_a_place_that_answers_with_no_key_is_the_third_sentence_never_saying_what_it_holds(
    home: Path, tmp_path: Path, fleet, made, seen
) -> None:
    other = _key(seen)
    holding = {
        "a fake secret": NOT_A_KEY + "\n",
        "empty": "",
        "white space": " \n\t\n",
        "two keys": f"{made.reveal()}\n{other.reveal()}\n",
        "age-keygen's comments and the key": f"# created: today\n# public key: age1x\n{made.reveal()}\n",
        "the key and a word after it": f"{made.reveal()} {WORD}\n",
        "more than a key's file": made.reveal() + "\n" + " " * vaultkey.MOST,
        "not text": "\udcff",
    }
    for case, text in holding.items():
        path = tmp_path / "holding"
        path.write_bytes(text.encode("utf-8", errors="surrogateescape"))
        path.chmod(0o600)
        with pytest.raises(vaultkey.WrongKey) as refused:
            _open(fleet, home, "", **{VARIABLE: str(path)})
        assert _refused(refused, seen) == (
            f"error: {path}, the file {VARIABLE} names holds no age key, so it does not open the fleet's vault: the"
            " file holds the key alone, one line that starts AGE-SECRET-KEY-1; nothing else is tried"
        ), case


def test_the_key_s_line_may_end_in_white_space_of_either_system(home: Path, tmp_path: Path, fleet, made, seen) -> None:
    for text in (made.reveal(), made.reveal() + "\r\n", "  " + made.reveal() + "\n\n"):
        path = _put(tmp_path / "with-ends", text)
        assert _open(fleet, home, "", **{VARIABLE: str(path)}).reveal() == made.reveal()


def test_a_file_the_variable_names_is_read_and_never_run(home: Path, tmp_path: Path, fleet, made, seen) -> None:
    # Even where it may be run: a file that would leave a mark if it were run holds no key, and leaves none; a key in a
    # file that may be run is read as the key. Each is the person's alone (0700); one open to others as well (0755) is
    # refused for that before anything of it is read, and is never run either.
    mark = tmp_path / "ran"
    script = _put(tmp_path / "a-script", f"#!/bin/sh\n: > {mark}\necho {made.reveal()}\n", mode=0o700)
    with pytest.raises(vaultkey.WrongKey, match="holds no age key"):
        _open(fleet, home, "", **{VARIABLE: str(script)})
    assert not mark.exists()
    runnable = _put(tmp_path / "runnable-key", made.reveal() + "\n", mode=0o700)
    assert _open(fleet, home, "", **{VARIABLE: str(runnable)}).reveal() == made.reveal()
    for each in (script, runnable):
        each.chmod(0o755)
        with pytest.raises(vaultkey.Failed, match=r"is open to other accounts \(its mode is 0755\)"):
            _open(fleet, home, "", **{VARIABLE: str(each)})
    assert not mark.exists()


def _open_to_others(found: str, path: Path, mode: int) -> str:
    """The sentence for a key file open to the group or others: found as the sentence names the place."""
    return (
        f"error: {found} is open to other accounts (its mode is {mode:04o}), and the vault's key is read only from a"
        f" file you alone can read or change, as ssh reads a private key: chmod 600 {quoted(str(path))}; nothing else"
        " is tried"
    )


def test_a_key_file_open_to_other_accounts_is_refused_before_anything_of_it_is_read(
    home: Path, tmp_path: Path, fleet, made, monkeypatch, seen
) -> None:
    # The owner's answer of 2026-11-23 to review 23 (D8.md 2): the key that opens every secret is taken only from a file
    # this account alone can read or change, as ssh takes a private key (any of 077 refused), whichever place names it:
    # one who could write it could put a key of theirs in its place. Each file below holds the key that opens the
    # vault, so the refusal is the mode's alone; nothing of the file is read, and it is left as it is.
    given = _put(tmp_path / "given-key", made.reveal() + "\n")
    named = _put(tmp_path / "a folder of mine" / "named key", made.reveal() + "\n")
    places = {
        "the variable's file": (f"{given}, the file {VARIABLE} names", given, "", {VARIABLE: str(given)}),
        "vault_key_file's file": (f"{named}, the file vault_key_file names", named, str(named), {}),
        "the home's own key file": (f"the key file {paths.key_file(fleet)}", paths.key_file(fleet), "", {}),
    }
    real, reads = os.read, []

    def recorded(handle: int, most: int) -> bytes:
        reads.append(handle)
        return real(handle, most)

    for case, (found, path, key_file, more) in places.items():
        before = path.read_bytes()
        # Readable by the group or by others; writable by them and not readable; runnable by them.
        for mode in (0o640, 0o604, 0o644, 0o620, 0o602, 0o711):
            path.chmod(mode)
            reads.clear()
            with monkeypatch.context() as patched:
                patched.setattr(os, "read", recorded)
                with pytest.raises(vaultkey.Failed) as refused:
                    _open(fleet, home, key_file, **more)
            assert _refused(refused, seen) == _open_to_others(found, path, mode), case
            assert reads == [], f"{case} at {mode:04o} was read"
            assert path.read_bytes() == before and stat.S_IMODE(path.stat().st_mode) == mode, case
        # The person's alone, to read and write, to read alone, or to run too: taken.
        for mode in (0o600, 0o400, 0o700):
            path.chmod(mode)
            assert _open(fleet, home, key_file, **more).reveal() == made.reveal(), (case, mode)
    # A path a shell would split is said as one word, as chmod is to be given it.
    assert "chmod 600 '" in _open_to_others("x", named, 0o644)


def test_every_mode_that_gives_the_group_or_others_any_bit_of_a_key_file_is_refused(
    home: Path, tmp_path: Path, fleet, made, seen
) -> None:
    # Every one of the 256 modes in which the person may read the file, the group's and others' 64 beside each of the
    # person's own four: refused where the group or others have any bit, to read, write or run (ssh's 077), and taken
    # only where they have none, whatever the person's own bits.
    given = _put(tmp_path / "given-key", made.reveal() + "\n")
    modes = list(range(0o400, 0o1000))
    refused, taken = [], []
    for mode in modes:
        given.chmod(mode)
        try:
            assert _open(fleet, home, "", **{VARIABLE: str(given)}).reveal() == made.reveal()
        except vaultkey.Failed as error:
            assert str(error) == _open_to_others(f"{given}, the file {VARIABLE} names", given, mode)
            refused.append(mode)
        else:
            taken.append(mode)
    # Writable by the group or by others and not readable, or runnable by them, as readable: each refused.
    named = {0o620, 0o602, 0o711, 0o640, 0o604, 0o644}
    assert named <= set(refused), sorted(f"{mode:04o} taken" for mode in named - set(refused))
    assert taken == [0o400, 0o500, 0o600, 0o700], [f"{mode:04o}" for mode in taken]
    assert refused == [mode for mode in modes if mode & 0o077] and len(refused) == 252


def test_the_key_file_made_here_is_taken_whatever_the_umask(home: Path, fleet, seen) -> None:
    # make_key makes its file the person's alone, so the rule never refuses the key init makes, under a umask that would
    # have made any other file open to all.
    was = os.umask(0)
    try:
        key = _saw(seen, vaultkey.make_key(paths.key_file(fleet)))
    finally:
        os.umask(was)
    assert stat.S_IMODE(paths.key_file(fleet).stat().st_mode) == 0o600
    assert _open(fleet, home).reveal() == key.reveal()


def test_ansible_s_vault_password_file_is_never_read_and_is_no_refusal(
    home: Path, tmp_path: Path, fleet, made, seen
) -> None:
    other = _key(seen)
    for text in (other.reveal() + "\n", NOT_A_KEY + "\n"):
        ansible = _put(tmp_path / "vault_pass", text, mode=0o755)
        assert _open(fleet, home, "", ANSIBLE_VAULT_PASSWORD_FILE=str(ansible)).reveal() == made.reveal()
    paths.key_file(fleet).rename(tmp_path / "put-aside")
    with pytest.raises(vaultkey.NoKey) as refused:
        _open(fleet, home, "", ANSIBLE_VAULT_PASSWORD_FILE=str(tmp_path / "vault_pass"))
    assert "ANSIBLE" not in _refused(refused, seen)


# ---------------------------------------------------------------- no place at all ----


def test_with_no_place_the_first_sentence_says_what_would_open_the_vault(
    home: Path, tmp_path: Path, fleet, seen
) -> None:
    key_file = paths.key_file(fleet)
    assert vaultkey.source(fleet, key_file="", key_command=(), environ=_env(home)) is None
    init = said("{init}", {"init": Line("init")})
    settings = said("{settings}", {"settings": Line("settings", {"name": "vault_key_file", "value": PLACEHOLDER})})
    # No vault yet: init makes the fleet and its key, in the key file vault_key_file names, never the variable's.
    with pytest.raises(vaultkey.NoKey) as refused:
        _open(fleet, home)
    sentence = _refused(refused, seen)
    assert sentence == (
        f"error: nothing here opens the fleet's vault: it has none yet, and the key file {key_file} (vault_key_file) is"
        f" not there: {init} makes the fleet, its key there and its vault"
    )
    assert VARIABLE not in sentence
    # The person's own file, named and not there, is the one said.
    own = tmp_path / "keys" / "one"
    with pytest.raises(vaultkey.NoKey) as refused:
        _open(fleet, home, str(own))
    assert f"the key file {own} (vault_key_file) is not there" in _refused(refused, seen)
    # A vault there whose key is not: the fleet's folder was copied, and its key is to be copied apart from it, or
    # named in vault_key_file, never in the variable, which is a script's and the agents' server's.
    sealed.create(fleet, _key(seen))
    with pytest.raises(vaultkey.NoKey) as refused:
        _open(fleet, home)
    sentence = _refused(refused, seen)
    assert sentence == (
        f"error: nothing here opens {sealed.path(fleet)}: the key file {key_file} (vault_key_file) is not there, and"
        " vault_key_command names no command: copy the key file of the computer that made the fleet to"
        f" {key_file}, or name the file that holds it in vault_key_file: {settings}"
    )
    assert VARIABLE not in sentence
    # An empty variable is none.
    with pytest.raises(vaultkey.NoKey):
        _open(fleet, home, **{VARIABLE: ""})


def test_a_key_that_opens_with_no_vault_yet_is_given_back_once_it_is_an_age_key(
    home: Path, tmp_path: Path, fleet, seen
) -> None:
    key = vaultkey.make_key(paths.key_file(fleet))
    _saw(seen, key)
    assert not sealed.path(fleet).exists()
    assert _open(fleet, home).reveal() == key.reveal()
    # The person's own key file, the one vault_key_file names, is the key file too.
    own = vaultkey.make_key(tmp_path / "keys" / "own")
    _saw(seen, own)
    assert _open(fleet, home, str(tmp_path / "keys" / "own")).reveal() == own.reveal()


def _unmade(fleet: paths.Home, named: str, undo: str, nothing: str) -> str:
    init = said("{init}", {"init": Line("init")})
    return (
        f"error: {named}, and the fleet has no vault yet: a new fleet's vault is sealed to the key in its own key file,"
        f" {paths.key_file(fleet)} (vault_key_file), which {init} makes, and never to one named elsewhere: {undo} for"
        f" {init}; {nothing}"
    )


def test_with_no_vault_yet_the_variable_s_key_is_refused_and_the_key_file_kept(
    home: Path, tmp_path: Path, fleet, seen
) -> None:
    # Review 23 (D8.md 1): with no vault, open gave back the variable's key, over the home's own key file, and the
    # vault init made was sealed to it with no word. A new fleet's vault is sealed to its own key file's key alone.
    mine = vaultkey.make_key(paths.key_file(fleet))
    _saw(seen, mine)
    kept = paths.key_file(fleet).read_bytes()
    theirs = _key(seen)
    given = _put(tmp_path / "another-fleet-s-key", theirs.reveal() + "\n")
    folder = tmp_path / "a-folder"
    folder.mkdir()
    # Whatever the variable's file is, or is not: nothing is read there, so no refusal of its own is said.
    for path in (given, tmp_path / "missing", folder, _fifo(tmp_path / "a-pipe")):
        for key_file in ("", str(paths.key_file(fleet))):
            with pytest.raises(vaultkey.NoVaultYet) as refused:
                _open(fleet, home, key_file, **{VARIABLE: str(path)})
            assert _refused(refused, seen) == _unmade(
                fleet, f"{VARIABLE} names {path}", f"unset {VARIABLE}", "nothing is read"
            )
    assert not sealed.path(fleet).exists()
    assert paths.key_file(fleet).read_bytes() == kept
    # Unset, the key file's key is given back, the vault is sealed to it, and the variable's key opens nothing.
    assert _open(fleet, home).reveal() == mine.reveal()
    sealed.create(fleet, mine)
    with pytest.raises(vaultkey.WrongKey):
        _open(fleet, home, **{VARIABLE: str(given)})
    assert sealed.read(fleet, _open(fleet, home)) == {}


def test_with_no_vault_yet_a_command_is_refused_and_never_run(home: Path, tmp_path: Path, fleet, seen) -> None:
    mark = tmp_path / "ran"
    program = _put(tmp_path / "program", f"#!/bin/sh\n: > {mark}\n", mode=0o755)
    for made in (False, True):
        if made:
            _saw(seen, vaultkey.make_key(paths.key_file(fleet)))
        with pytest.raises(vaultkey.NoVaultYet) as refused:
            _open(fleet, home, "", key_command=[str(program), COMMAND_WORD])
        sentence = _refused(refused, seen)
        assert sentence == _unmade(
            fleet, "vault_key_command names a command", "take it out of client.yml", "nothing is run"
        )
        assert str(program) not in sentence and not mark.exists()


def test_a_key_from_the_variable_is_given_back_only_for_a_vault_it_opened(
    home: Path, tmp_path: Path, fleet, monkeypatch, seen
) -> None:
    # A vault seen when the place was looked at, and gone when it was read: the variable's key is refused all the same.
    theirs = _key(seen)
    given = _put(tmp_path / "another-fleet-s-key", theirs.reveal() + "\n")
    looked, vault = os.path.lexists, str(sealed.path(fleet))
    monkeypatch.setattr(os.path, "lexists", lambda path: str(path) == vault or looked(path))
    with pytest.raises(vaultkey.NoVaultYet) as refused:
        _open(fleet, home, **{VARIABLE: str(given)})
    assert _refused(refused, seen) == _unmade(
        fleet, f"{VARIABLE} names {given}", f"unset {VARIABLE}", "nothing is read"
    )


def test_the_vault_s_own_refusals_are_its_own(home: Path, fleet, made, seen) -> None:
    # A vault that is no vault is sealed.py's to say, whatever the key.
    sealed.path(fleet).write_text("no vault\n")
    with pytest.raises(sealed.Unreadable) as refused:
        _open(fleet, home)
    assert not isinstance(refused.value, vaultkey.WrongKey)
    assert "is not an armored age file" in _refused(refused, seen)


# ---------------------------------------------------------------- where a key file is never kept ----


def _release(where: Path) -> Path:
    """A program root that is an installed release: a clone of its tag, with release.json."""
    where.mkdir(parents=True)
    (where / "justfile").write_text("")
    (where / ".git").mkdir()
    (where / "release.json").write_text("{}\n")
    return where


def test_a_key_file_named_where_the_key_is_never_kept_is_refused_by_name(
    home: Path, tmp_path: Path, fleet, seen
) -> None:
    fleet_dir, state = paths.fleet_dir(fleet), paths.state(fleet)
    fleet_dir.joinpath("hosts").mkdir(parents=True)
    state.mkdir()
    (tmp_path / "linked-fleet").symlink_to(fleet_dir)
    misplaced = {
        fleet_dir / "vault-key": (fleet_dir, "the fleet's folder, which is copied to another computer"),
        fleet_dir / "hosts" / "lab" / "key": (fleet_dir, "the fleet's folder, which is copied to another computer"),
        tmp_path / "linked-fleet" / "key": (fleet_dir, "the fleet's folder, which is copied to another computer"),
        state / "key": (state, "the state folder, which a cleanup may remove"),
        fleet_dir: (fleet_dir, "the fleet's folder, which is copied to another computer"),
    }
    for path, (folder, why) in misplaced.items():
        for named, more, key_file in (("vault_key_file", {}, str(path)), (VARIABLE, {VARIABLE: str(path)}, "")):
            with pytest.raises(vaultkey.Misplaced) as refused:
                _open(fleet, home, key_file, **more)
            assert _refused(refused, seen) == (
                f"error: {named} is {path}, inside {folder}, {why}: the vault's key is kept apart from it, so nothing"
                " is read there; name a file outside it"
            )
    # Beside client.yml, at the top of a named home or a checkout's .aw/, is where the default is: taken.
    beside = _put(home / "my-key", "x\n")
    assert vaultkey.place(fleet, str(beside), environ=_env(home)) == beside
    assert vaultkey.place(fleet, "~/my-key", environ=_env(home)) == beside


def test_the_installed_program_s_key_is_never_in_its_config_folder(tmp_path: Path, seen) -> None:
    environ = {"HOME": str(tmp_path / "user")}
    where = paths.home(_release(tmp_path / "release"), environ)
    config = where.root
    assert (
        where.how == "installed" and paths.key_file(where) == tmp_path / "user/.local/share/agent-warehouse/vault-key"
    )
    for path in (config / "vault-key", config / "keys" / "one"):
        with pytest.raises(vaultkey.Misplaced) as refused:
            vaultkey.place(where, str(path), environ=environ)
        assert _refused(refused, seen) == (
            f"error: vault_key_file is {path}, inside {config}, the installed program's config folder, which people"
            " publish: the vault's key is kept apart from it, so nothing is read there; name a file outside it"
        )
    state = paths.state(where)
    with pytest.raises(vaultkey.Misplaced, match="the state folder, which a cleanup may remove"):
        vaultkey.place(where, str(state / "key"), environ=environ)
    assert vaultkey.place(where, "", environ=environ) == paths.key_file(where)


def test_a_value_that_is_no_path_is_refused_and_never_said(home: Path, fleet, seen) -> None:
    # A key pasted where its file's path goes would be on the screen.
    pasted = _key(seen).reveal()
    for key_file, more, named in ((pasted, {}, "vault_key_file"), ("", {VARIABLE: pasted}, VARIABLE)):
        with pytest.raises(vaultkey.Failed) as refused:
            _open(fleet, home, key_file, **more)
        assert _refused(refused, seen) == (
            f"error: {named} is not an absolute path: name the file that holds the vault's key from / or from ~, and"
            " the value given is not said"
        )
    # ~ is the person's home.
    assert vaultkey.place(fleet, "~/keys/one", environ=_env(home)) == home / "keys" / "one"
    found = vaultkey.source(fleet, key_file="", key_command=(), environ=_env(home, **{VARIABLE: "~/keys/two"}))
    assert found == vaultkey.Source(vaultkey.OVERRIDE, home / "keys" / "two")


# ---------------------------------------------------------------- with nobody at the terminal ----


CHILD = """\
import sys
sys.path.insert(0, {lib!r})
from claude_vm.fleet import paths, sealed, vaultkey
where = paths.home({repo!r})
key = vaultkey.open(where, key_file="", key_command=())
print(sorted(sealed.read(where, key)), sys.stdin.read() == "")
"""


def test_a_vault_made_under_a_key_made_here_opens_in_a_child_with_no_terminal(home: Path, fleet, made, seen) -> None:
    # No terminal and nothing on standard input: the run opens the vault as a run at a terminal does, asking nothing.
    child = subprocess.run(
        [sys.executable, "-I", "-c", CHILD.format(lib=str(LIB), repo=str(REPO))],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=environment(home),
        start_new_session=True,
        timeout=120,
        check=False,
    )
    assert (child.returncode, child.stdout, child.stderr) == (0, "['github_token'] True\n", "")


# ---------------------------------------------------------------- what the module holds to ----


def test_vaultkey_imports_pyrage_only_when_it_needs_it_and_the_standard_library_else(home: Path) -> None:
    added = imports_of(module="claude_vm.fleet.vaultkey", home=home) - imports_of(module="claude_vm.fleet", home=home)
    assert "claude_vm.fleet.vaultkey" in added, sorted(added)
    assert sorted({name.split(".")[0] for name in added} - set(sys.stdlib_module_names) - {"claude_vm"}) == []
    assert not added & {"asyncio", "subprocess"}


def _texts() -> dict[str, str]:
    """Every module of lib/claude_vm/ and every program of tools/, by its path in the checkout."""
    found = {str(path.relative_to(REPO)): path.read_text() for path in sorted((LIB / "claude_vm").rglob("*.py"))}
    for path in sorted((REPO / "tools").iterdir()):
        if path.is_file():
            try:
                found[str(path.relative_to(REPO))] = path.read_text()
            except UnicodeDecodeError:
                continue
    return found


# The modules beside vaultkey.py that name its VARIABLE, in one line of their own that defines a constant and nowhere
# else in their text: the line, and why. Each gives it to a run and reads no key, which a test of its own holds by the
# module's syntax.
GIVE_THE_VARIABLE = {
    "lib/claude_vm/cli/mcp_serve.py": (
        'KEY_VARIABLE = "AGENT_WAREHOUSE_VAULT_KEY_FILE"',
        "the agents' server gives each run it starts the variable from its registered arguments, never from its own"
        " environment, names the constant only as a key of the run's environment, and opens no file:"
        " tests/unit/test_aw_mcp_server.py::test_the_server_gives_the_variables_to_a_run_alone_and_opens_no_file",
    ),
}


def _past_the_given(rel: str, text: str) -> str:
    """text, past the one line of GIVE_THE_VARIABLE's where rel is one of its modules."""
    if rel not in GIVE_THE_VARIABLE:
        return text
    return "\n".join(line for line in text.splitlines() if line != GIVE_THE_VARIABLE[rel][0])


def test_a_run_reads_the_key_only_through_vaultkey() -> None:
    texts = _texts()
    assert len(texts) > 100, len(texts)
    # The variable's name in vaultkey.py alone; the key file's name in paths.py and vaultkey.py alone (an option such
    # as --vault-key-file is not it); Ansible's variable in no module of the new engine.
    variable = sorted(rel for rel, text in texts.items() if VARIABLE in _past_the_given(rel, text))
    assert variable == ["lib/claude_vm/fleet/vaultkey.py"]
    assert all(VARIABLE in texts[rel] for rel in GIVE_THE_VARIABLE), "an exception that names nothing any more"
    named = {rel for rel, text in texts.items() if re.search(r"\bvault-key\b(?!-)", text)}
    assert "lib/claude_vm/fleet/paths.py" in named
    assert named <= {"lib/claude_vm/fleet/paths.py", "lib/claude_vm/fleet/vaultkey.py"}, sorted(named)
    engine = [rel for rel in texts if rel.startswith(tuple(f"lib/claude_vm/{package}/" for package in NEW))]
    assert len(engine) > 40, len(engine)
    ansible = "ANSIBLE_VAULT_PASSWORD_FILE"
    assert [rel for rel in engine if any(ansible in text for text in _code_strings(texts[rel]))] == []
    # The scan reads the code's strings, never a docstring: vaultkey.py's says the variable is never read.
    assert ansible in texts["lib/claude_vm/fleet/vaultkey.py"]
    module = f"'''{ansible}'''\nX = 1\ndef f():\n    '''{ansible}'''\n    return '{ansible}'\n"
    assert _code_strings(module) == [ansible]


def _code_strings(text: str) -> list[str]:
    """Every string constant of a module's code, its docstrings and those of its classes and functions left out."""
    tree = ast.parse(text)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]
