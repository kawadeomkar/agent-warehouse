"""aw's command line read against the one table, and the launcher at the checkout's top that starts it
(lib/claude_vm/cli/, lib/claude_vm/schema/check.py, aw; docs/architecture/engine.md §16.3).

Every option of every verb parses as --long value and as --long=value, --set given twice and -v counted; a prefix of an
option is an option the verb has not got; a value outside its pattern, one with a newline after it and an option the
verb has not got are refused before the dispatch, in one sentence of the project's that says no value back, and never
with argparse's own text: no refusal has a usage block. A verb no card has built is refused naming that card, and just
<verb> only where today's justfile has the recipe. --detach is on exactly the verbs whose run is a job. No names means
the default VM or every VM as the reference says, with no fleet file read. An exported RUN_LOCK or ATTENDED changes
nothing, in run_words and in the program; --attended=1 before the verb does, and no word makes a run take no lock. A
Python that is no
virtualenv's is refused for every verb, naming PYTHON. A refusal imports no Ansible and no module of the engine.

The launcher, run as a person runs it, through the kit: through a link in another folder, through a relative link in a
folder that is itself a link, and from inside another checkout it runs its own checkout's tools/aw; the Python is the
one the checkout's folder picks, never the working folder's - not a version manager's choice there, not another
project's virtualenv, not a module planted there; with no environment yet it makes the checkout's own, with the
stand-in uv given today's just install's words, once, puts the git hook in place and then runs the program; aw install
--lock is uv lock alone; core.hooksPath set, no hook; --install-env only from a job; a virtualenv of the person's own is
never installed into unasked; a Python that cannot be run, no uv and a uv that made no Python are its own sentences;
and its Python version is the justfile's.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from aw_kit import LAUNCHER, environment, imports_of, on_path, run_aw, run_launcher
from harness import GIT_ENV, REPO

from claude_vm.cli import before, dispatch, habits, parse
from claude_vm.cli.main import main
from claude_vm.fleet import paths
from claude_vm.schema import check as checks
from claude_vm.schema import rules
from claude_vm.schema.patterns import PATTERNS, full
from claude_vm.schema.words import EXITS, VERBS, Verb, said

# Today's justfile's recipes a person runs, whose verb names just <verb> while no card has built it, until the flip.
TODAYS = {
    "help",
    "init",
    "install",
    "vault",
    "deploy",
    "code",
    "login",
    "status",
    "settings",
    "destroy",
    "host",
    "logs",
    "mcp",
}
# A value a person may have typed where it does not belong: no sentence may say it back.
TYPED = "swordfish"
# A value no pattern takes: a control character in it.
BAD = f"{TYPED}\x07"
# One value each pattern takes, for an option or a verb's words that names it.
SAMPLES = {
    "vm_name": "alpha",
    "vm_names": "alpha,beta",
    "host_name": "lab2",
    "current_host": "lab2",
    "cores": "8",
    "memory": "16G",
    "disk": "64G",
    "repos": "app,lib",
    "roles": "claude_code",
    "stage": "configure",
    "sign_in": "github",
    "sections": "vms,work",
    "project": "app",
    "session": "0a608ad8-1b2c-4d3e-8f90-123456789abc",
    "setting_name": "vm_cores",
    "job_id": "0123456789abcdef",
    "editor": "zed",
    "verb": "deploy",
    "text": "two words",
    "path": "/srv/aw/key",
    "attended": "1",
    "address": "pve.example",
    "proxmox_id": "local-lvm",
    "token_user": "ansible@pve",
    "github_user": "octocat",
    "secret_file": "/srv/aw/secret",
    "on_off": "on",
    "fingerprint": ":".join(["AB"] * 32),
    "assignment": "vm_cores=4",
    "secret_name": "github_token",
    "ssh_fingerprint": "SHA256:" + "A" * 43,
    "release": "v3.0.0",
    "dependency_group": "mcp",
}
# What a refusal never has: argparse's own usage block.
USAGE = "usage:"
# The modules a refusal never loads.
HEAVY = ("pydantic", "ansible", "yaml", "claude_vm.engine")


def _spelled(option) -> str:
    return checks.spelled(option)


def _options():
    """Every verb's options as the parser takes them, --detach among them, each with its verb."""
    return [(verb, option) for verb in VERBS.values() for option in checks.options_of(verb)]


def _refused(words: list[str]) -> str:
    with pytest.raises(parse.Refused) as refused:
        parse.read(words)
    return refused.value.sentence


def _said_nothing_typed(sentence: str) -> None:
    assert sentence.startswith("error: aw") and "\n" not in sentence, sentence
    assert TYPED not in sentence and USAGE not in sentence, sentence


# ---------------------------------------------------------------- the parser, in process ----


def test_every_pattern_has_a_sample_it_takes() -> None:
    assert set(SAMPLES) == set(PATTERNS)
    assert [name for name, value in SAMPLES.items() if not full(name, value)] == []


@pytest.mark.parametrize(("verb", "option"), _options(), ids=lambda item: getattr(item, "name", None) or _spelled(item))
def test_every_option_of_every_verb_parses_in_both_spellings(monkeypatch, verb: Verb, option) -> None:
    # The parser alone: an option given on its own may break a rule between options (--tags with no name, --value with
    # no setting), which tests/unit/test_aw_cli_rules.py holds.
    monkeypatch.setattr(rules, "run", lambda codes, values: None)
    spelling, field = _spelled(option), option.field
    if option.kind == "flag":
        assert parse.read([verb.name, spelling]).values == {field: True}
        return
    if option.kind == "counted":
        assert parse.read([verb.name, f"-{option.short * 3}"]).values == {field: 3}
        assert parse.read([verb.name, spelling, spelling]).values == {field: 2}
        return
    sample = SAMPLES[option.pattern]
    taken = [sample] if option.kind == "repeatable" else sample
    spellings = [spelling, f"-{option.short}"] if option.short else [spelling]
    for given in spellings:
        assert parse.read([verb.name, given, sample]).values == {field: taken}, given
    assert parse.read([verb.name, f"{spelling}={sample}"]).values == {field: taken}
    if option.kind == "repeatable":
        assert parse.read([verb.name, spelling, sample, f"{spelling}={sample}"]).values == {field: [sample, sample]}


def test_set_twice_and_v_counted_beside_the_words() -> None:
    reading = parse.read(["deploy", "alpha", "--set", "vm_cores=4", "-vvv", "beta", "--set=vm_memory_mb=4096"])
    assert reading.values == {"names": ["alpha", "beta"], "set": ["vm_cores=4", "vm_memory_mb=4096"], "verbosity": 3}


def test_the_words_of_the_done_line_reach_the_dispatch_as_given() -> None:
    reading = parse.read(["deploy", "--cores", "8", "alpha"])
    values, said = habits.names(VERBS["deploy"], reading.values)
    run, values = before.run_words(reading.before, values, {})
    assert (reading.verb, values, said, run) == ("deploy", {"cores": "8", "names": ["alpha"]}, [], before.RunWords())


@pytest.mark.parametrize("verb", [verb for verb in VERBS.values() if any(option.long for option in verb.options)])
def test_a_prefix_of_an_option_is_an_option_the_verb_has_not_got(verb: Verb) -> None:
    longs = {option.long for option in checks.options_of(verb) if option.long}
    for long in sorted(longs):
        prefix = long[:-1]
        if prefix and prefix not in longs:
            sentence = _refused([verb.name, f"--{prefix}", "x"])
            assert sentence == checks.no_option(verb.name, f"--{prefix}"), sentence


@pytest.mark.parametrize(
    ("verb", "option"),
    [(verb, option) for verb, option in _options() if option.pattern is not None],
    ids=lambda item: getattr(item, "name", None) or _spelled(item),
)
def test_a_value_outside_its_pattern_or_with_a_newline_after_it_is_refused_unsaid(verb: Verb, option) -> None:
    spelling = _spelled(option)
    for value in (BAD, SAMPLES[option.pattern] + "\n" + TYPED, SAMPLES[option.pattern] + "\n"):
        sentence = _refused([verb.name, f"{spelling}={value}"])
        _said_nothing_typed(sentence)
        # The option's help, and the form its value may take, which the person reads in aw help too (H25).
        assert spelling in sentence and said(option.help or "") in sentence, sentence
        assert sentence.endswith(f" [pattern: '{PATTERNS[option.pattern]}']"), sentence


@pytest.mark.parametrize("verb", [verb for verb in VERBS.values() if verb.words and verb.words.pattern])
def test_a_word_outside_its_pattern_is_refused_unsaid(verb: Verb) -> None:
    for word in (BAD, SAMPLES[verb.words.pattern] + "\n"):
        sentence = _refused([verb.name, word])
        _said_nothing_typed(sentence)
        assert said(verb.words.help) in sentence, sentence
        assert sentence.endswith(f" [pattern: '{PATTERNS[verb.words.pattern]}']"), sentence


@pytest.mark.parametrize(
    "words",
    [
        ["deploy", f"--bogus={TYPED}"],
        ["deploy", "-x", TYPED],
        ["status", "--detach"],
        ["deploy", "--cores"],
        ["deploy", f"--check={TYPED}"],
        ["deploy", f"-v={TYPED}"],
        ["deploy", "--cores", "8", f"--cores={TYPED}"],
        ["deploy", "--check", "--check"],
        # A second name where one is taken, which no rule of today's says back (code's and help's do: H5's rules).
        ["release", "alpha", "beta"],
        ["release", "alpha,beta"],
        ["logs", "0123456789abcdef", "fedcba9876543210"],
        ["init", TYPED],
        ["install", TYPED],
        ["control", TYPED],
        ["control", "--detach"],
        [TYPED],
        ["--bogus", "deploy"],
        [f"--attended={TYPED}", "deploy"],
        ["--attended"],
        [f"--detached-run={TYPED}", "deploy"],
        ["--no-lock", "deploy"],
    ],
)
def test_a_refusal_is_one_sentence_of_the_project_s_that_says_nothing_typed(words: list[str]) -> None:
    try:
        reading = parse.read(words)
        habits.names(VERBS[reading.verb], reading.values)
    except parse.Refused as refused:
        _said_nothing_typed(refused.sentence)
    else:
        pytest.fail(f"{words} was not refused")


def test_each_refusal_says_what_was_wrong() -> None:
    assert _refused(["deploy", "--bogus=x"]) == checks.no_option("deploy", "--bogus")
    assert _refused(["deploy", f"--{TYPED}"]) == checks.no_option("deploy", f"--{TYPED}")
    assert _refused(["deploy", "--Bogus"]) == checks.no_option("deploy", None)
    assert _refused([TYPED]) == checks.no_such_command()
    assert _refused(["install", "x"]) == checks.only_options("install")
    # init's word, and help's second, are refused by today's rules, in today's sentences (H5's, schema/rules.py).
    assert _refused(["init", "x"]) == rules.RULES["init.1"].check({"extra": "x"})
    assert _refused(["logs", "0123456789abcdef", "fedcba9876543210"]) == checks.one_word("logs", VERBS["logs"].words)
    assert "takes --cores once" in _refused(["deploy", "--cores=8", "--cores=9"])
    assert "--check takes no value" in _refused(["deploy", "--check=yes"])
    assert "--cores takes a value: --cores CORES, or --cores=CORES" in _refused(["deploy", "--cores"])
    assert checks.no_option("", "--bogus").endswith("has no --bogus option before its command (aw help lists them)")


def test_control_is_an_unknown_verb_since_the_add_on_s_verbs_are_not_the_base_s(capsys) -> None:
    # The owner's answer 3 of 2026-11-20: control is the add-on's, and aw control is refused as a word that is no
    # command is, before anything runs and whatever follows it: never "not built yet", which would name a card.
    for words in (["control"], ["control", "--detach"], ["control", TYPED], ["--attended=1", "control"]):
        assert _refused(words) == checks.no_such_command(), words
    assert checks.check("control", {}) == checks.no_such_command()
    assert main([str(REPO / "tools" / "aw"), "control"]) == EXITS["refused"]
    said = capsys.readouterr()
    assert (said.out, said.err) == ("", checks.no_such_command() + "\n")


def test_a_word_that_starts_with_a_dash_is_given_with_equals() -> None:
    assert parse.read(["settings", "vm_cores", "--value=-1"]).values == {"name": "vm_cores", "value": "-1"}
    assert "--value takes a value" in _refused(["settings", "vm_cores", "--value", "-1x"])


@pytest.mark.parametrize(
    ("words", "verb"),
    [
        (["deploy", "--", "--check"], "deploy"),
        (["deploy", "--", "--cores", "9"], "deploy"),
        (["deploy", "--cores", "8", "--", "--cores", "9"], "deploy"),
        (["deploy", "--", "-alpha"], "deploy"),
        (["deploy", "alpha", "--"], "deploy"),
        (["settings", "vm_cores", "--value", "--"], "settings"),
        (["--", "deploy"], ""),
        (["--attended", "--", "status"], ""),
        (["--"], ""),
    ],
)
def test_an_end_of_options_is_refused_before_argparse_reads_it(monkeypatch, words: list[str], verb: str) -> None:
    # argparse's intermixed parse would take -- and drop it, and read the words after it as options all the same
    # (deploy -- --check was a dry run): no verb takes a word that starts with -, so -- is refused wherever it stands,
    # and the sentence says how a value that starts with - is given.
    read = parse.argparse.ArgumentParser.parse_known_intermixed_args

    def watched(self, args=None, namespace=None):
        assert "--" not in (args or []), "argparse was given --"
        return read(self, args, namespace)

    monkeypatch.setattr(parse.argparse.ArgumentParser, "parse_known_intermixed_args", watched)
    sentence = _refused(words)
    assert sentence == parse.no_end_of_options(verb)
    assert "--option=value" in sentence
    _said_nothing_typed(sentence)


def test_a_value_of_two_dashes_is_given_with_equals() -> None:
    assert parse.read(["settings", "vm_cores", "--value=--"]).values == {"name": "vm_cores", "value": "--"}
    assert _refused(["bogus", "--"]) == checks.no_such_command()


def test_no_verb_is_help() -> None:
    assert parse.read([]).verb == parse.read(["--attended=1"]).verb == "help"


def test_detach_is_on_exactly_the_verbs_whose_run_is_a_job(monkeypatch) -> None:
    # The parser alone: code with no VM breaks a rule between options (code.1).
    monkeypatch.setattr(rules, "run", lambda codes, values: None)
    for verb in VERBS.values():
        if verb.changes:
            assert parse.read([verb.name, "--detach"]).values == {"detach": True}, verb.name
        else:
            assert _refused([verb.name, "--detach"]) == checks.no_option(verb.name, "--detach")


def test_the_check_takes_json_with_exact_types_and_coerces_nothing() -> None:
    given = {"cores": "8", "names": ["alpha"], "start": True, "set": ["vm_cores=4"], "verbosity": 2, "detach": True}
    assert checks.check("deploy", given) is None
    verb = "deploy"
    assert checks.check(verb, {"cores": 8}) == f"error: aw {verb} --cores takes a string"
    assert checks.check("deploy", {"check": "true"}) is not None
    assert checks.check("deploy", {"verbosity": True}) is not None
    assert checks.check("deploy", {"names": "alpha"}) is not None
    assert checks.check("deploy", {"detach": True}) is None and checks.check("status", {"detach": True}) is not None
    assert checks.check("deploy", {"cores": None}) is None
    assert checks.check("deploy", {"no_such": "x"}) == checks.no_option("deploy", "no_such")
    assert checks.check(TYPED, {}) == checks.no_such_command()
    assert checks.check_before({"attended": "1", "detached_run": True}) is None
    assert checks.check_before({"no_lock": True}) is not None
    assert checks.check_before({"attended": "2"}) is not None


# ---------------------------------------------------------------- the habits ----


def test_a_name_s_underscore_is_taken_as_a_hyphen_and_said_and_commas_split_names() -> None:
    reading = parse.read(["deploy", "alpha_b,gamma", "beta"])
    values, said = habits.names(VERBS["deploy"], reading.values)
    assert values == {"names": ["alpha-b", "gamma", "beta"]}
    assert said == [habits.HYPHENATED.format(name="alpha_b", changed="alpha-b")]
    assert said[0].startswith("warning: Proxmox does not allow underscores in VM names, so alpha_b was changed to")
    values, said = habits.names(VERBS["code"], parse.read(["code", "my_vm"]).values)
    assert (values, len(said)) == ({"name": "my-vm"}, 1)


@pytest.mark.parametrize(
    ("words", "default_vm"),
    [
        (["deploy"], True),
        (["deploy", "--only", "provision"], True),
        (["deploy", "--only", "configure"], False),
        (["deploy", "--only", "ssh-config"], False),
        (["deploy", "--only", "template"], False),
        (["deploy", "--check"], False),
        (["destroy"], True),
        (["login"], False),
        (["status"], False),
    ],
)
def test_no_names_means_the_default_vm_or_every_vm_as_the_reference_says(monkeypatch, words, default_vm) -> None:
    # Read from no file of the fleet: the default VM's name is the handler's to read, never the parser's.
    def no_fleet(*args, **kwargs):
        raise AssertionError("the parser read the fleet's files")

    monkeypatch.setattr(paths, "home", no_fleet)
    monkeypatch.setattr(paths, "_pointer", no_fleet)
    values, _ = habits.names(VERBS[words[0]], parse.read(words).values)
    assert values["default_vm"] is default_vm
    if "template" in words:
        # The template takes no names: a rule between options says so (deploy.9).
        with pytest.raises(parse.Refused):
            parse.read([*words, "alpha"])
        return
    assert "default_vm" not in habits.names(VERBS[words[0]], parse.read([*words, "alpha"]).values)[0]


def test_a_verb_that_takes_one_name_is_given_no_default() -> None:
    assert habits.names(VERBS["code"], {})[0] == {}
    assert habits.names(VERBS["status"], {"only": "vms"})[0] == {"only": "vms", "default_vm": False}


# ---------------------------------------------------------------- the run's own words ----


def test_an_exported_run_lock_or_attended_changes_nothing_and_the_words_before_the_verb_do() -> None:
    environ = {"RUN_LOCK": "", "ATTENDED": "1", "LOG_DIR": "/srv/logs", "LOG_RETENTION_DAYS": "3"}
    run, values = before.run_words(parse.read(["deploy", "alpha"]).before, {"names": ["alpha"]}, environ)
    assert (run.attended, run.log_dir, run.log_retention_days) == (False, "/srv/logs", "3")
    reading = parse.read(["--attended=1", "--detached-run", "deploy", "--detach", "-vv", "--set=a=1"])
    run, values = before.run_words(reading.before, reading.values, {})
    assert run == before.RunWords(verbosity=2, set=("a=1",), detach=True, attended=True, detached_run=True)
    assert values == {}
    assert before.run_words(parse.read(["--attended", "0", "status"]).before, {}, {})[0].attended is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.attended = False  # type: ignore[misc]
    # No word, and no field of the run's own, makes a run take no lock.
    assert "no_lock" not in {field.name for field in dataclasses.fields(before.RunWords)}


def test_an_exported_run_lock_or_attended_changes_nothing_in_the_program_either(monkeypatch) -> None:
    # Held where it can break, in main, after run_words: the dispatch is a recorder of the run's own words it gets.
    received: list[before.RunWords] = []

    def recorder(program_root, verb, run, values) -> int:
        received.append(run)
        return EXITS["done"]

    monkeypatch.setattr(dispatch, "dispatch", recorder)
    for name in before.ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ATTENDED", "1")
    monkeypatch.setenv("RUN_LOCK", "")
    assert main([str(REPO / "tools" / "aw"), "status"]) == EXITS["done"]
    assert main([str(REPO / "tools" / "aw"), "--attended=1", "status"]) == EXITS["done"]
    assert received == [before.RunWords(), before.RunWords(attended=True)]


# ---------------------------------------------------------------- the dispatch ----


def _with_handler(name: str) -> Verb:
    return dataclasses.replace(VERBS[name], handler="engine.test:run")


@pytest.mark.parametrize("verb", [verb for verb in VERBS.values() if verb.handler is None], ids=lambda verb: verb.name)
def test_a_verb_no_card_has_built_names_its_card_and_just_only_where_today_has_it(monkeypatch, verb) -> None:
    monkeypatch.setattr(dispatch, "lacking", lambda: [])
    with pytest.raises(parse.Refused) as refused:
        dispatch.dispatch(REPO, verb, before.RunWords(), {})
    sentence = refused.value.sentence
    assert sentence == dispatch.not_built(verb) and f"(card {verb.card} builds it)" in sentence
    assert (f"just {verb.name}" in sentence) is (verb.name in TODAYS), sentence


def test_the_recipes_named_are_today_s() -> None:
    assert dispatch.JUST_RECIPES == TODAYS
    assert {"cancel", "release"} <= set(VERBS) - TODAYS


def test_a_changing_verb_with_no_fleet_names_aw_init_and_says_which_fleet_it_drives(monkeypatch, home, capsys) -> None:
    called = []
    monkeypatch.setattr(dispatch, "lacking", lambda: [])
    monkeypatch.setattr(dispatch, "_load", lambda handler: lambda run, **values: called.append((run, values)) or 3)
    run = before.RunWords(verbosity=1)
    with pytest.raises(parse.Refused) as refused:
        dispatch.dispatch(REPO, _with_handler("deploy"), run, {"names": ["alpha"]})
    assert refused.value.sentence == f"error: {paths.fleet_dir(paths.home(REPO))} missing. Run: aw init"
    assert called == []
    # init and install come before any fleet.
    for name in ("init", "install"):
        assert dispatch.dispatch(REPO, _with_handler(name), run, {}) == 3
    paths.fleet_dir(paths.home(REPO)).mkdir(parents=True)
    assert dispatch.dispatch(REPO, _with_handler("deploy"), run, {"names": ["alpha"]}) == 3
    assert called[-1] == (run, {"names": ["alpha"]})
    said = capsys.readouterr().err.splitlines()
    assert said[-1] == f"aw drives the fleet in {home}, which {paths.VARIABLE} names", said
    # A verb whose run is no job is handed on without a look at the fleet; a handler's None is done.
    monkeypatch.setattr(dispatch, "_load", lambda handler: lambda run, **values: None)
    shutil.rmtree(paths.fleet_dir(paths.home(REPO)))
    assert dispatch.dispatch(REPO, _with_handler("status"), run, {}) == EXITS["done"]
    assert capsys.readouterr().err == ""


def test_a_fleet_a_client_yml_points_at_is_said_and_the_default_is_not(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dispatch, "lacking", lambda: [])
    monkeypatch.setattr(dispatch, "_load", lambda handler: lambda run, **values: 0)
    checkout, elsewhere = tmp_path / "checkout", tmp_path / "elsewhere"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "justfile").write_text("")
    (checkout / ".aw" / "fleet").mkdir(parents=True)
    (elsewhere / "fleet").mkdir(parents=True)
    environ = {"HOME": str(tmp_path / "person")}
    said: list[str] = []

    class Err:
        def write(self, text: str) -> None:
            said.append(text)

    assert dispatch.dispatch(checkout, _with_handler("login"), before.RunWords(), {}, environ=environ, err=Err()) == 0
    assert "".join(said) == ""
    (checkout / ".aw" / "client.yml").write_text(f"fleet_home: {elsewhere}\n")
    assert dispatch.dispatch(checkout, _with_handler("login"), before.RunWords(), {}, environ=environ, err=Err()) == 0
    client = checkout / ".aw" / "client.yml"
    assert "".join(said) == f"aw drives the fleet in {elsewhere}, which fleet_home in {client} names\n"


def test_an_environment_without_the_packages_names_aw_install_but_for_install_and_help(monkeypatch, capsys) -> None:
    assert dispatch.lacking(("claude_vm", "no_such_module_of_aw")) == ["no_such_module_of_aw"]
    monkeypatch.setattr(dispatch, "lacking", lambda: ["ansible"])
    with pytest.raises(parse.Refused) as refused:
        dispatch.dispatch(REPO, VERBS["status"], before.RunWords(), {})
    assert refused.value.sentence.endswith("lacks ansible, which uv.lock pins: run aw install"), refused.value.sentence
    with pytest.raises(parse.Refused) as refused:
        dispatch.dispatch(REPO, VERBS["install"], before.RunWords(), {})
    assert refused.value.sentence == dispatch.not_built(VERBS["install"])
    # help, built (H6), prints the table without them.
    assert dispatch.dispatch(REPO, VERBS["help"], before.RunWords(), {"recipe": "deploy"}) == EXITS["done"]
    assert capsys.readouterr().out.startswith("Usage: aw deploy [<name>...]\n")


def test_a_python_that_is_no_virtualenv_s_is_refused_for_every_verb_before_anything_else(monkeypatch) -> None:
    assert dispatch.in_a_virtualenv() is (sys.prefix != sys.base_prefix) is True
    monkeypatch.setattr(dispatch, "in_a_virtualenv", lambda: False)
    monkeypatch.setattr(dispatch, "lacking", lambda: pytest.fail("the environment's packages were asked about"))
    for verb in VERBS.values():
        with pytest.raises(parse.Refused) as refused:
            dispatch.dispatch(REPO, _with_handler(verb.name), before.RunWords(), {})
        sentence = refused.value.sentence
        assert sentence == dispatch.NO_VIRTUALENV.format(python=sys.executable), verb.name
    assert "PYTHON" in sentence and "aw install" not in sentence


def test_main_exits_1_on_a_refusal_and_says_the_habit_first(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dispatch, "lacking", lambda: [])
    assert main([str(REPO / "tools" / "aw"), "deploy", "my_vm"]) == EXITS["refused"] == 1
    said = capsys.readouterr()
    assert said.out == ""
    assert said.err.splitlines() == [
        habits.HYPHENATED.format(name="my_vm", changed="my-vm"),
        dispatch.not_built(VERBS["deploy"]),
    ]


# ---------------------------------------------------------------- tools/aw and the launcher, as a person runs them ----

REFUSED = [
    ("deploy", "--core", "8"),
    ("deploy", "--cores", BAD),
    ("deploy", BAD),
    ("deploy", f"--bogus={TYPED}"),
    ("deploy", f"--check={TYPED}"),
    ("release", "alpha", TYPED),
    ("init", TYPED),
    (TYPED,),
    (f"--attended={TYPED}", "status"),
    ("deploy", "--cores", "8", "alpha"),
    ("cancel", "0123456789abcdef"),
]


@pytest.mark.parametrize("words", REFUSED, ids=lambda words: " ".join(words).replace("\x07", "^G"))
def test_tools_aw_and_the_launcher_refuse_in_one_line_that_says_nothing_typed(home, words) -> None:
    launched = run_launcher(*words, home=home, cwd=home, first_on_path=(on_path(home),))
    for ran in (run_aw(*words, home=home), launched):
        assert (ran.returncode, ran.stdout) == (1, ""), ran
        [line] = ran.stderr.splitlines()
        _said_nothing_typed(line)


def test_the_done_lines_are_said_as_the_card_says(home) -> None:
    # aw on PATH is the launcher, so its sentences name aw, as in this process.
    path = (on_path(home),)
    built = run_launcher("deploy", "--cores", "8", "alpha", home=home, cwd=REPO, first_on_path=path)
    assert built.stderr == dispatch.not_built(VERBS["deploy"]) + "\n" and "(card G3 builds it)" in built.stderr
    core = run_launcher("deploy", "--core", "8", home=home, cwd=REPO, first_on_path=path)
    assert (core.returncode, core.stderr) == (1, checks.no_option("deploy", "--core") + "\n")


@pytest.mark.parametrize("words", [("deploy", "--core", "8"), ("deploy", "alpha"), (TYPED,), ("deploy", BAD)])
def test_a_refusal_imports_no_ansible_and_no_module_of_the_engine(home, words) -> None:
    loaded = imports_of(*words, home=home)
    assert "claude_vm.cli.parse" in loaded
    assert [name for name in loaded if name.split(".")[0] in HEAVY[:3] or name.startswith(HEAVY[3])] == []


# ---------------------------------------------------------------- the launcher, in checkouts a test makes ----

# A tools/aw that says which checkout it is, with what it was given and how.
PROGRAM = """#!/usr/bin/env python3
import json
import sys

print(json.dumps({{"checkout": {name!r}, "argv": sys.argv, "isolated": sys.flags.isolated, "prefix": sys.prefix}}))
"""
# A python3 first on PATH that is no virtualenv's: tools/env-run then names the checkout's own environment.
NO_VIRTUALENV = "#!/bin/sh\nexit 1\n"
JOB = "0123456789abcdef"
# git that reads no config of the developer's.
NO_GIT_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(checkout: Path, *args: str) -> None:
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", *args], cwd=checkout, env=env, check=True, capture_output=True, timeout=60)


def _checkout(where: Path, name: str, *, program: str = "stand-in") -> Path:
    """A checkout a test makes: a git repository with the launcher and the tools it runs, copied, and a tools/aw that
    says which checkout it is - or, with program="real", this checkout's own tools/aw and lib/."""
    checkout = where / name
    (checkout / "tools").mkdir(parents=True)
    _git(checkout, "init", "--quiet")
    shutil.copy2(LAUNCHER, checkout / "aw")
    for tool in ("env-run", "git-hook", "stale_scripts.py"):
        shutil.copy2(REPO / "tools" / tool, checkout / "tools" / tool)
    shutil.copy2(REPO / "uv.lock", checkout / "uv.lock")
    (checkout / "justfile").write_text("# a justfile above, which aw never runs\n")
    if program == "real":
        shutil.copy2(REPO / "tools" / "aw", checkout / "tools" / "aw")
        (checkout / "lib").symlink_to(REPO / "lib")
    else:
        (checkout / "tools" / "aw").write_text(PROGRAM.format(name=name))
    return checkout


def _uv(where: Path) -> Path:
    """A uv that records each run - its words, the two variables it is given and where it ran - and, for a sync into
    a folder under the test's home (the checkout's own environment), makes the environment's python: a wrapper of the
    suite's own Python. It never writes outside the test's home."""
    uv = where / "uv-bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text(
        "#!/bin/sh\n"
        f'{{ for word in "$@"; do printf "%s\\n" "$word"; done; echo --end--; }} >> "{where}/uv.args"\n'
        f'echo "UV_PROJECT_ENVIRONMENT=${{UV_PROJECT_ENVIRONMENT:-}}" >> "{where}/uv.env"\n'
        f'echo "UV_PYTHON_DOWNLOADS=${{UV_PYTHON_DOWNLOADS:-}}" >> "{where}/uv.env"\n'
        f'echo "PWD=$(pwd -P)" >> "{where}/uv.env"\n'
        'if [ "$1" = sync ]; then\n'
        "  case ${UV_PROJECT_ENVIRONMENT:-} in\n"
        '  "$HOME"/*)\n'
        '    mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        f'    printf \'#!/bin/sh\\nexec "%s" "$@"\\n\' "{sys.executable}" > "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        '    chmod 755 "$UV_PROJECT_ENVIRONMENT/bin/python" ;;\n'
        "  esac\n"
        "fi\n"
    )
    uv.chmod(0o755)
    return uv


def _uv_env(where: Path) -> list[str]:
    """The two variables the first run of uv was given."""
    return (where / "uv.env").read_text().splitlines()[:2]


def _uv_runs(where: Path) -> list[list[str]]:
    record = where / "uv.args"
    text = record.read_text() if record.exists() else ""
    return [run.splitlines() for run in text.split("--end--\n")[:-1]]


def _managed(where: Path, **more: str) -> dict[str, object]:
    """What has a launcher run with no environment yet, as run_launcher's arguments: PYTHON unset and a python3 first
    on PATH that is no virtualenv's, the stand-in uv, and git that reads no config of the developer's."""
    bin_dir = where / "no-virtualenv"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "python3").write_text(NO_VIRTUALENV)
    (bin_dir / "python3").chmod(0o755)
    env = {"PYTHON": "", "UV": str(where / "uv-bin" / "uv"), **NO_GIT_CONFIG, **more}
    return {"env": env, "first_on_path": (bin_dir,)}


def _ran(result: subprocess.CompletedProcess[str]) -> dict:
    """What the stand-in tools/aw said it was given: its one line on standard output, which nothing else wrote to."""
    [line] = result.stdout.splitlines()
    return json.loads(line)


def test_the_launcher_runs_its_own_checkout_through_a_link_and_from_inside_another(home, tmp_path) -> None:
    own, other = _checkout(tmp_path, "own"), _checkout(tmp_path, "other")
    (other / "deep" / "er").mkdir(parents=True)
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    (elsewhere / "aw").symlink_to(os.path.relpath(own / "aw", elsewhere))
    (elsewhere / "via").symlink_to(elsewhere / "aw")
    for launcher in (elsewhere / "aw", elsewhere / "via", own / "aw"):
        result = run_launcher("status", "--json", home=home, launcher=launcher, cwd=other / "deep" / "er")
        assert result.returncode == 0, result.stderr
        said = _ran(result)
        assert said == {
            "checkout": "own",
            "argv": [str(own.resolve() / "tools" / "aw"), "status", "--json"],
            "isolated": 1,
            "prefix": sys.prefix,
        }, launcher
    # Run by a relative path from its own folder, as ./aw.
    assert _ran(run_launcher("help", home=home, launcher="./aw", cwd=own))["checkout"] == "own"


def test_through_a_relative_link_in_a_folder_that_is_a_link_the_target_s_checkout_runs(home, tmp_path) -> None:
    # a/linkdir is a/deep/sub, where aw is ../../own/aw: the kernel reads that as a/own/aw. Read as text,
    # a/linkdir/../.. is the test's own folder, where a decoy checkout stands at own/.
    target = _checkout(tmp_path / "a", "own")
    decoy = _checkout(tmp_path, "own")
    (decoy / "tools" / "aw").write_text(PROGRAM.format(name="decoy"))
    sub = tmp_path / "a" / "deep" / "sub"
    sub.mkdir(parents=True)
    (sub / "aw").symlink_to(Path("..") / ".." / "own" / "aw")
    (tmp_path / "a" / "linkdir").symlink_to(sub, target_is_directory=True)
    result = run_launcher("status", home=home, launcher=tmp_path / "a" / "linkdir" / "aw", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    said = _ran(result)
    assert (said["checkout"], said["argv"][0]) == ("own", str(target.resolve() / "tools" / "aw"))


def _virtualenv(where: Path) -> Path:
    """A virtualenv the test makes, with no packages: its python."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(where)], check=True, timeout=120)
    return where / "bin" / "python"


def _picks_by_folder(where: Path) -> Path:
    """A folder holding a python3 that a version manager picks by the folder it runs in, as pyenv's shim does: the
    Python a .python-version names, in that folder or the nearest above it up to where; else the suite's own Python's
    base, which is no virtualenv's."""
    base = Path(sys.base_prefix) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    assert base.is_file(), base
    bin_dir = where / "picks-by-folder"
    bin_dir.mkdir()
    shim = bin_dir / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        f"top='{where.resolve()}'\n"
        "dir=$(pwd -P)\n"
        "while :; do\n"
        '  if [ -f "$dir/.python-version" ]; then exec "$(cat "$dir/.python-version")" "$@"; fi\n'
        '  case $dir in "$top" | /) break ;; esac\n'
        '  dir=$(dirname "$dir")\n'
        "done\n"
        f'exec "{base}" "$@"\n'
    )
    shim.chmod(0o755)
    return bin_dir


@pytest.mark.parametrize(
    "how",
    [
        "python3 here is no virtualenv",
        "python3 here is another project's",
        "PYTHON a relative path",
        "PYTHON a version manager's shim",
    ],
)
def test_the_python_is_the_one_the_checkout_s_folder_picks_and_never_the_working_folder_s(home, tmp_path, how) -> None:
    # The checkout's folder picks its virtualenv, venv/; the project's folder the launcher is run from has one of its
    # own at the same relative path, which its version manager picks but in the first case. Neither is installed into.
    checkout = _checkout(tmp_path, "picked")
    ours = _virtualenv(checkout / "venv")
    project = tmp_path / "project"
    theirs = _virtualenv(project / "venv")
    _uv(tmp_path)
    env = {"PYTHON": "", "UV": str(tmp_path / "uv-bin" / "uv"), **NO_GIT_CONFIG}
    shims = _picks_by_folder(tmp_path)
    first_on_path: tuple[Path, ...] = (shims,)
    (checkout / ".python-version").write_text(f"{ours}\n")
    if how != "python3 here is no virtualenv":
        (project / ".python-version").write_text(f"{theirs}\n")
    if how == "PYTHON a relative path":
        env["PYTHON"], first_on_path = "venv/bin/python", ()
    elif how == "PYTHON a version manager's shim":
        env["PYTHON"], first_on_path = str(shims / "python3"), ()
    result = run_launcher(
        "status", home=home, launcher=checkout / "aw", cwd=project, env=env, first_on_path=first_on_path
    )
    assert result.returncode == 0, result.stderr
    assert Path(_ran(result)["prefix"]).resolve() == (checkout / "venv").resolve()
    assert (result.stderr, _uv_runs(tmp_path)) == ("", [])
    assert not (checkout / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.parametrize("python_set", [True, False], ids=["PYTHON set", "python3 a virtualenv's"])
def test_a_module_in_the_folder_it_is_run_from_never_runs(home, tmp_path, python_set) -> None:
    # A repository a person cloned and runs aw in: python puts the folder it runs in first on sys.path, and sysconfig
    # is not loaded at start, so a sysconfig.py there would run wherever that folder asks which Python it is.
    checkout = _checkout(tmp_path, "planted")
    cloned = tmp_path / "cloned"
    cloned.mkdir()
    mark = tmp_path / "planted-ran"
    (cloned / "sysconfig.py").write_text(f"open({str(mark)!r}, 'w').write('planted sysconfig ran')\n")
    bin_dir = tmp_path / "virtualenv-python3"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "python3").chmod(0o755)
    env = {} if python_set else {"PYTHON": ""}
    result = run_launcher("status", home=home, launcher=checkout / "aw", cwd=cloned, env=env, first_on_path=(bin_dir,))
    assert result.returncode == 0, result.stderr
    assert (_ran(result)["checkout"], _ran(result)["prefix"]) == ("planted", sys.prefix)
    assert not mark.exists(), mark.read_text()


def test_with_no_environment_the_launcher_makes_the_checkout_s_own_then_the_hook_then_runs(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "fresh")
    _uv(tmp_path)
    managed = _managed(tmp_path)
    data = subprocess.check_output([checkout / "tools" / "env-run", "--data"], env=environment(home), text=True)
    venv = Path(data.strip()) / "venv"
    result = run_launcher("status", "--json", home=home, launcher=checkout / "aw", cwd=tmp_path, **managed)
    assert result.returncode == 0, result.stderr
    # Today's just install's words for the checkout's own environment, from the checkout's top, once.
    assert _uv_runs(tmp_path) == [["sync", "--locked", "--managed-python", "--python", "3.13"]]
    assert _uv_env(tmp_path) == [f"UV_PROJECT_ENVIRONMENT={venv}", "UV_PYTHON_DOWNLOADS=automatic"]
    assert (tmp_path / "uv.env").read_text().splitlines()[2] == f"PWD={checkout.resolve()}"
    hook = checkout / ".git" / "hooks" / "pre-commit"
    assert hook.read_bytes() == (REPO / "tools" / "git-hook").read_bytes() and os.access(hook, os.X_OK)
    assert "This checkout has no Python environment yet" in result.stderr
    assert "Installed git's pre-commit hook." in result.stderr
    # Then the program, with that environment's Python, and nothing of the install on standard output.
    assert _ran(result)["argv"][1:] == ["status", "--json"]
    again = run_launcher("status", home=home, launcher=checkout / "aw", cwd=tmp_path, **managed)
    assert again.returncode == 0 and again.stderr == "" and len(_uv_runs(tmp_path)) == 1


def test_install_lock_is_uv_lock_alone(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "lock")
    _uv(tmp_path)
    result = run_launcher("install", "--lock", home=home, launcher=checkout / "aw", **_managed(tmp_path))
    assert result.returncode == 0, result.stderr
    assert _uv_runs(tmp_path) == [["lock"]]
    assert _uv_env(tmp_path) == ["UV_PROJECT_ENVIRONMENT=", "UV_PYTHON_DOWNLOADS=never"]
    assert result.stdout == "" and not (checkout / ".git" / "hooks" / "pre-commit").exists()


def test_install_says_how_to_put_aw_on_path_where_aw_on_path_is_not_this_launcher(home, tmp_path) -> None:
    # The owner's answer 4 of 2026-11-21: one sentence, and nothing written outside the checkout for it (H25).
    checkout = _checkout(tmp_path, "hint")
    _uv(tmp_path)
    env = {"PYTHON": sys.executable, "UV": str(tmp_path / "uv-bin" / "uv"), "AGENT_WAREHOUSE_JOB": JOB, **NO_GIT_CONFIG}
    told = run_launcher("--install-env", home=home, launcher=checkout / "aw", env=env)
    link = f"ln -s '{checkout.resolve() / 'aw'}' ~/.local/bin/aw"
    assert told.returncode == 0, told.stderr
    assert f"To run aw from any folder, link it into a folder on your PATH: {link}" in told.stdout.splitlines()
    linked = on_path(home, checkout / "aw")
    again = run_launcher("--install-env", home=home, launcher=checkout / "aw", env=env, first_on_path=(linked,))
    assert again.returncode == 0 and "To run aw from any folder" not in again.stdout + again.stderr


def test_a_core_hooks_path_of_yours_gets_no_hook_and_is_said(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "hooks-path")
    _uv(tmp_path)
    yours = tmp_path / "your-hooks"
    yours.mkdir()
    hooks = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(yours)}
    result = run_launcher("status", home=home, launcher=checkout / "aw", **_managed(tmp_path, **hooks))
    assert result.returncode == 0, result.stderr
    assert "warning: git's core.hooksPath is set, so aw install put no git hook there" in result.stderr
    assert list(yours.iterdir()) == [] and not (checkout / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.parametrize("job", ["", "0123", "0123456789ABCDEF", f"{JOB}\n"])
def test_install_env_is_refused_but_from_a_job(home, tmp_path, job) -> None:
    checkout = _checkout(tmp_path, "no-job")
    _uv(tmp_path)
    env = {"PYTHON": sys.executable, "UV": str(tmp_path / "uv-bin" / "uv"), "AGENT_WAREHOUSE_JOB": job}
    result = run_launcher("--install-env", home=home, launcher=checkout / "aw", env=env)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == "error: --install-env is aw install's own step, which its run starts: run aw install\n"
    assert _uv_runs(tmp_path) == []


def test_install_env_from_a_job_syncs_your_virtualenv_as_just_install_does(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "job")
    _uv(tmp_path)
    env = {"PYTHON": sys.executable, "UV": str(tmp_path / "uv-bin" / "uv"), "AGENT_WAREHOUSE_JOB": JOB, **NO_GIT_CONFIG}
    result = run_launcher("--install-env", home=home, launcher=checkout / "aw", env=env)
    assert result.returncode == 0, result.stderr
    # --inexact: the environment is yours, and nothing in it the lock does not list is removed; no Python downloaded.
    assert _uv_runs(tmp_path)[0][:5] == ["sync", "--locked", "--inexact", "--python", sys.executable]
    assert _uv_env(tmp_path)[:2] == [f"UV_PROJECT_ENVIRONMENT={sys.prefix}", "UV_PYTHON_DOWNLOADS=never"]
    assert (checkout / ".git" / "hooks" / "pre-commit").exists()
    locked = run_launcher("--install-env", "--lock", home=home, launcher=checkout / "aw", env=env)
    assert locked.returncode == 0 and _uv_runs(tmp_path)[-1] == ["lock"]


def test_your_virtualenv_without_the_packages_is_never_installed_into_unasked(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "yours", program="real")
    _uv(tmp_path)
    venv = tmp_path / "your-venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    env = {"PYTHON": str(venv / "bin" / "python"), "UV": str(tmp_path / "uv-bin" / "uv")}
    result = run_launcher("status", home=home, launcher=checkout / "aw", env=env)
    assert (result.returncode, result.stdout) == (1, "")
    [line] = result.stderr.splitlines()
    # Run by its path, with no aw on PATH that is it: the program as it was started (H25).
    program = shlex.quote(str(checkout / "aw"))
    assert line.startswith("error: the Python environment") and line.endswith(f"run {program} install"), line
    assert _uv_runs(tmp_path) == [] and not (checkout / ".git" / "hooks" / "pre-commit").exists()


def test_a_python_that_is_no_virtualenv_s_is_refused_naming_python(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "system", program="real")
    _uv(tmp_path)
    base = Path(sys.base_prefix) / "bin" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    # As it says itself: the path it was started by, which the launcher gave it.
    executable = subprocess.run(
        [base, "-I", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()
    env = {"PYTHON": str(base), "UV": str(tmp_path / "uv-bin" / "uv")}
    result = run_launcher("status", home=home, launcher=checkout / "aw", env=env)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == dispatch.NO_VIRTUALENV.format(python=executable) + "\n"
    assert _uv_runs(tmp_path) == [] and not (checkout / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.parametrize("named", ["/nonexistent/aw-test/python", "no-such-python-of-aw", "./tools", "tools/git-hook"])
def test_a_python_that_cannot_be_run_is_the_launcher_s_sentence(home, tmp_path, named) -> None:
    # Not there, a name no folder on PATH has, a folder, a file that is no program: each the checkout's where relative.
    checkout = _checkout(tmp_path, "unrunnable")
    (checkout / "tools" / "git-hook").chmod(0o644)
    result = run_launcher("status", home=home, launcher=checkout / "aw", env={"PYTHON": named})
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == (
        f"error: the Python {named} cannot be run: set PYTHON to a virtualenv's python, or leave it unset and aw makes"
        " this checkout's own\n"
    )


def test_the_launcher_says_where_there_is_no_uv_and_where_uv_made_no_python(home, tmp_path) -> None:
    checkout = _checkout(tmp_path, "no-uv")
    data = subprocess.check_output([checkout / "tools" / "env-run", "--data"], env=environment(home), text=True)
    python = Path(data.strip()) / "venv" / "bin" / "python"
    result = run_launcher("status", home=home, launcher=checkout / "aw", **_managed(tmp_path, UV="no-such-uv-of-aw"))
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.splitlines()[-1] == (
        "error: no no-such-uv-of-aw: install uv (https://docs.astral.sh/uv/getting-started/installation/), or name it"
        " with UV="
    )
    makes_nothing = tmp_path / "makes-nothing" / "uv"
    makes_nothing.parent.mkdir()
    makes_nothing.write_text("#!/bin/sh\nexit 0\n")
    makes_nothing.chmod(0o755)
    result = run_launcher("status", home=home, launcher=checkout / "aw", **_managed(tmp_path, UV=str(makes_nothing)))
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.splitlines()[-1] == (
        f"error: uv made no Python at {python}: run aw install again, and read what it says"
    )


def test_the_launcher_s_python_version_is_the_justfile_s() -> None:
    launcher = re.search(r"(?m)^managed_python=(\S+)$", LAUNCHER.read_text())
    justfile = re.search(r"(?m)^managed_python := '([^']+)'$", (REPO / "justfile").read_text())
    assert launcher and justfile and launcher[1] == justfile[1]


def test_the_launcher_is_posix_sh_and_executable() -> None:
    text = LAUNCHER.read_text()
    assert text.startswith("#!/bin/sh\n") and stat.S_IMODE(LAUNCHER.stat().st_mode) & 0o111
    # It finds its checkout from its own path, never the working directory.
    assert '"$checkout/tools/aw"' in text and "$PWD" not in text and "git rev-parse --show-toplevel" not in text
