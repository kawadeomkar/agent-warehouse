"""The cutover's check of the words: the table of the new command line (lib/claude_vm/schema/words.py) against the
contract of today's (tests/unit/cli_contract.json), through the two maps beside the contract
(tests/unit/cli_contract_changes.py).

Every verb, option, short form, flag and help sentence of the contract is in the table as it is, or CHANGED says why
not; every setting given before a recipe today is kept from the environment (KEPT) or changed; every word the table has
and the contract lacks is in NEW with its source, so that no word nobody ruled on comes in unseen; no map names a word
that is not so; and each word the trims took out of the base is absent. A Ctrl-C at one of the playbook's prompts, where
today asks Continue or Abort and aw ends the run, is a behaviour BEHAVIOURS names, found as the exit statuses are. It
goes with the contract at the cutover.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli_contract_changes import BEHAVIOURS, CHANGED, KEPT, NEW

from claude_vm.schema.patterns import PATTERNS
from claude_vm.schema.words import BEFORE, EXITS, STATUS_EXITS, VERBS, Option

CONTRACT = json.loads((Path(__file__).resolve().parent / "cli_contract.json").read_text())
RECIPES = {recipe["name"]: recipe for recipe in CONTRACT["recipes"]}
SETTINGS = {setting["name"] for part in ("promised", "command_line") for setting in CONTRACT["settings"][part]}

# The words the trims and their verification took out of the base, each refused by its absence, and why.
TAKEN_OUT = {
    "init --export": "the owner's word 3: export, import and rekey are the add-on's",
    "init --import": "the owner's word 3: export, import and rekey are the add-on's",
    "init --rekey": "the owner's word 3: export, import and rekey are the add-on's",
    "init --take-over": "the owner's word 3: the take-over of an import is the add-on's",
    "init --recipient": "the owner's word 3: an export's recipient is the add-on's",
    "init --confirm": "the owner's word 3: an import's confirmation is the add-on's",
    "init --vault-key": "the owner's word 2: the vault's key comes from a file or a command, chosen by a setting",
    "vault --vault-key": "the owner's word 2: the vault's key comes from a file or a command, chosen by a setting",
    "init --audit-token-id": "T2: one token, kept to the pool, and no audit token",
    "init --audit-token-secret-file": "T2: one token, kept to the pool, and no audit token",
    "--run-lock": "T8: the locks live in the state folder, and --no-lock is the one word",
    "mcp --vault-password-file": "V2-8: --vault-key-file",
}


def _spelled(option: Option) -> str:
    return f"--{option.long}" if option.long else f"-{option.short}"


def _word(parameter: dict) -> str:
    """What the usage calls one of a recipe's words: its name in lower case, in the singular (NAMES: name)."""
    name = parameter["name"].lower()
    return name.removesuffix("s") if parameter["plural"] else name


def _key(recipe: str, parameter: dict) -> str:
    return f"{recipe} <{_word(parameter)}>" if parameter["words"] else f"{recipe} --{parameter['long']}"


def _table_words() -> set[str]:
    """Every word of the table, named as the maps name one."""
    words = {_spelled(option) for option in BEFORE}
    if any(verb.changes for verb in VERBS.values()):
        words.add("--detach")
    for verb in VERBS.values():
        words.add(verb.name)
        if verb.words is not None:
            words.add(f"{verb.name} <{verb.words.name}>")
        words |= {f"{verb.name} {_spelled(option)}" for option in verb.options}
    return words


def _contract_words() -> set[str]:
    """Every word of the contract, named as the maps name one: its verbs, options and words, and the settings given
    before the recipe."""
    words = {f"{name}=" for name in SETTINGS}
    for name, recipe in RECIPES.items():
        words.add(name)
        words |= {_key(name, parameter) for parameter in recipe["options"]}
    return words


def _patterns(name: str | None) -> list[str] | None:
    return None if name is None else [PATTERNS[name]]


def _differences() -> dict[str, tuple]:
    """Each verb, option or verb's words of the contract, or part of one, that the table does not have as it is: (the
    contract's, the table's). A word the table has not got at all is one entry, under the word's own name."""
    found: dict[str, tuple] = {}
    for name, recipe in RECIPES.items():
        verb = VERBS.get(name)
        if verb is None:
            found[name] = (name, None)
            continue
        for part in ("group", "doc"):
            if recipe[part] != getattr(verb, part):
                found[f"{name}: {part}"] = (recipe[part], getattr(verb, part))
        options = {option.long: option for option in verb.options if option.long}
        for parameter in recipe["options"]:
            key = _key(name, parameter)
            if parameter["words"]:
                if verb.words is None:
                    found[key] = (parameter, None)
                    continue
                today = {
                    "name": _word(parameter),
                    "plural": parameter["plural"],
                    "pattern": parameter["patterns"],
                    "help": parameter["help"],
                }
                table = {
                    "name": verb.words.name,
                    "plural": verb.words.plural,
                    "pattern": _patterns(verb.words.pattern),
                    "help": verb.words.help,
                }
            else:
                option = options.get(parameter["long"])
                if option is None:
                    found[key] = (parameter, None)
                    continue
                flag = parameter["value"] is not None
                today = {
                    "short": parameter["short"],
                    "kind": "flag" if flag else "value",
                    "pattern": parameter["patterns"],
                    "metavar": None if flag else parameter["name"],
                    "help": parameter["help"],
                }
                table = {
                    "short": option.short,
                    "kind": option.kind,
                    "pattern": _patterns(option.pattern),
                    "metavar": option.metavar,
                    "help": option.help,
                }
            found |= {f"{key}: {part}": (today[part], table[part]) for part in today if today[part] != table[part]}
    # The exit statuses the contract measured that the table names by meaning: a run a person cancelled, which today is
    # one Ctrl-C while the playbook runs, and one before it starts.
    for case, meaning in (("exit.ctrl-c-1", "cancelled"), ("exit.ctrl-c-2", "cancelled")):
        if _exit_row(case)["status"] != EXITS[meaning]:
            found[f"exit {meaning}"] = (_exit_row(case)["status"], EXITS[meaning])
    # And at one of the playbook's prompts, where today's one Ctrl-C asks Continue or Abort, C going on and A ending the
    # run: aw ends it there as anywhere else, a cancelled run.
    at_a_prompt = tuple(_exit_row(case)["status"] for case in ("exit.ctrl-c-3", "exit.ctrl-c-4"))
    if any(status != EXITS["cancelled"] for status in at_a_prompt):
        found["exit cancelled at a prompt"] = (at_a_prompt, EXITS["cancelled"])
    return found


def _exit_row(case: str) -> dict:
    """The contract's row of the exit statuses that a case measured."""
    (row,) = [row for row in CONTRACT["exits"] if f"(case {case})" in row["meaning"]]
    return row


def _changed_by(key: str) -> str | None:
    """The entry that says why a difference is one: CHANGED's or BEHAVIOURS' own, or CHANGED's of its word's whole."""
    return CHANGED.get(key) or BEHAVIOURS.get(key) or CHANGED.get(key.split(": ", 1)[0])


def test_every_word_of_the_contract_is_in_the_table_as_it_is_or_changed_with_its_source() -> None:
    unsaid = {key: what for key, what in _differences().items() if not _changed_by(key)}
    assert unsaid == {}, "a word of today's that the table changes: name it in CHANGED with its source"


def test_changed_names_only_what_the_table_changes() -> None:
    differences = _differences()
    stale = [key for key in CHANGED if key not in differences and key.removesuffix("=") not in SETTINGS]
    assert stale == [], "CHANGED names a word the table has as the contract has it, or no word of the contract"


def test_a_ctrl_c_at_a_prompt_is_a_behaviour_aw_has_otherwise_on_purpose() -> None:
    # Today C goes on (0) and A ends the run as a failed task (2); aw asks no question there, and ends the run with a
    # cancelled run's status. A behaviour of the same words, not a word changed: BEHAVIOURS names it, CHANGED does not.
    assert _differences()["exit cancelled at a prompt"] == ((0, 2), EXITS["cancelled"])
    assert "exit cancelled at a prompt" in BEHAVIOURS and set(BEHAVIOURS) & set(CHANGED) == set()


def test_behaviours_names_only_an_exit_status_the_table_changes() -> None:
    differences = _differences()
    stale = [key for key in BEHAVIOURS if key.startswith("exit ") and key not in differences]
    assert stale == [], "BEHAVIOURS names an exit status the table has as the contract measured it"


def test_every_setting_given_before_a_recipe_today_is_kept_or_changed() -> None:
    # The table takes no setting before its verb: each is read from the environment as today, or another word is.
    changed = {key.removesuffix("=") for key in CHANGED if key.endswith("=")}
    assert changed & set(KEPT) == set()
    assert sorted(changed | set(KEPT)) == sorted(SETTINGS)


def test_every_word_the_table_has_and_the_contract_lacks_is_new_with_its_source() -> None:
    new = _table_words() - _contract_words()
    words = {key for key in NEW if not key.startswith("rule ")}
    assert sorted(new - words) == [], "a word nobody ruled on: name it in NEW with its source"
    assert sorted(words - new) == [], "NEW names a word the table has not got, or one the contract has"


def test_every_source_is_said() -> None:
    assert [key for key, source in (CHANGED | BEHAVIOURS | KEPT | NEW | TAKEN_OUT).items() if not source.strip()] == []


@pytest.mark.parametrize("word", sorted(TAKEN_OUT))
def test_a_word_the_trims_took_out_of_the_base_is_absent(word: str) -> None:
    assert word not in _table_words()
    assert word not in NEW


def test_the_words_taken_out_are_what_their_rulings_name() -> None:
    # The add-on's words, the owner's word 2's, T2's and T8's, and the one V2-8 renamed: each absent above.
    assert {key.rsplit(" ", 1)[-1] for key in TAKEN_OUT} == {
        "--export",
        "--import",
        "--rekey",
        "--take-over",
        "--recipient",
        "--confirm",
        "--vault-key",
        "--audit-token-id",
        "--audit-token-secret-file",
        "--run-lock",
        "--vault-password-file",
    }


def test_every_rule_a_verb_names_is_the_contract_s_or_new() -> None:
    codes = {rule["code"] for rule in CONTRACT["rules"]}
    named = {code for verb in VERBS.values() for code in verb.rules}
    assert sorted(named - codes - {key.removeprefix("rule ") for key in NEW if key.startswith("rule ")}) == []


def test_the_exit_statuses_are_today_s_numbers() -> None:
    # A run that reached its playbook exits with the playbook's status today, which this check does not compare: aw's
    # failed run is new, said where it is settled. A Ctrl-C's is compared in _differences: aw's cancelled run exits 130,
    # which today is the status before the playbook starts and not while a task runs (99), as CHANGED says, nor at one
    # of its prompts, as BEHAVIOURS says.
    assert CONTRACT["exits"] and _differences()["exit cancelled"] == (99, EXITS["cancelled"])
    every = {name for name in RECIPES}
    rows = {(row["status"], tuple(sorted(row["recipes"]))) for row in CONTRACT["exits"]}
    assert (EXITS["done"], tuple(sorted(every))) in rows
    assert (EXITS["refused"], tuple(sorted(every))) in rows
    status = {row["status"] for row in CONTRACT["exits"] if row["recipes"] == ["status"]}
    assert status == {STATUS_EXITS[name] for name in ("nothing_wrong", "problem", "unread", "failed")}
    # A status a person cancelled exits as one whose read failed, today's 3 (lib/claude_vm/status/engine.py's
    # _end_for_an_interrupt): a number of the contract's for status, so nothing of status's exit statuses is changed.
    assert STATUS_EXITS["cancelled"] == STATUS_EXITS["failed"] == 3
