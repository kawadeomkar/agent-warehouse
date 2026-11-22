"""The command line's patterns have one home, and one function holds a value to one (lib/claude_vm/schema/patterns.py;
docs/architecture/engine.md §16.2).

Each pattern takes and refuses the values given here, most of them what today's tests give the same pattern of the
justfile, and refuses each value it takes with a newline after it: `full` holds a value whole, never up to a newline.
Each has its form in words and its examples: every example is held by its pattern and a near miss of each, written
here, is not; no words hold a pattern's own characters; and `form` says the words and the first example, or the words
alone where the pattern shows none, never the pattern itself (the owner's ruling of 2026-11-22, review/code/OWNER-9.md
2, in the plan's folder). The module imports the standard library alone.
"""

from __future__ import annotations

import re
import sys

import pytest
from aw_kit import imports_of

from claude_vm.schema import patterns
from claude_vm.schema.patterns import PATTERNS, form, full

MODULE = "claude_vm.schema.patterns"

SESSION_ID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
FINGERPRINT = ":".join(["AB"] * 32)
# An SSH key's SHA256 fingerprint as ssh-keygen -l prints it: SHA256: and 43 characters of base64, with no padding.
SSH_FINGERPRINT = "SHA256:" + "uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s"
# test_justfile.py's test_a_given_value_outside_its_characters_is_refused_before_anything_runs, for each of init's
# options with a pattern of characters: --address, --host, --token-user, --token-id, --token-secret-file,
# --github-user, --storage and --bridge.
OUTSIDE_INIT_S = ["a'b", 'a"b', "a b", "$(x)", "a`b", "a;b", "a\nb", "ålpha"]

# What each pattern takes and refuses: (taken, refused). Each value comes from today's test that gives it to the same
# pattern - tests/unit/test_justfile.py, unless the comment names test_vm_size.py, test_vm_repos.py or tools/check's own
# check of the same value behind the pattern - or, where today's tests give that pattern none, is marked as this
# file's own. A value taken here that today's run then refuses is refused by a rule, not by the pattern: said beside it.
# An option's pattern takes the empty value, since an empty value is no value given.
CASES: dict[str, tuple[list[str], list[str]]] = {
    "vm_name": (
        # NAME_WORDS; the underscore taken as a hyphen; destroy's names; status's be_ta; the stage words as names
        # (refused without a terminal by a rule, which asks which was meant); tools/check names' alpha and b-2.c-d,e.
        [
            "alpha",
            "beta",
            "gamma",
            "alpha,beta",
            "a.b-c-d",
            "duscope_1",
            "my_vm",
            "be_ta",
            "configure",
            "check",
            "provision",
            "ssh-config",
            "alpha,configure",
            "alpha,check",
            "alpha,ssh-config",
            "b-2.c-d,e",
        ],
        # BAD_NAMES; test_a_value_is_never_run_as_code; make's TAGS= spelling; tools/check names' a\b, the empty name
        # and ,a.
        [
            "a b",
            "a'b",
            'a"b',
            "a;b",
            "$(x)",
            "{{x}}",
            "a`b",
            "a\tb",
            "alpha\nbeta",
            "ålpha",
            "a%",
            "a:b",
            "-x",
            ".hidden",
            "$(touch ran)",
            "`touch ran`",
            "TAGS=claude_code",  # make's spelling of --tags
            "a\\b",
            "",
            ",a",
        ],
    ),
    "vm_names": (
        # test_destroy_confirm_answers_the_question_for_exactly_the_vms_named: each refused there by the rule that
        # --confirm names exactly the VMs given, never by the pattern.
        ["", "alpha", "gamma", "beta,my_vm", "alpha,beta"],
        # This file's own.
        ["a b", "-x", ",a", "$(x)"],
    ),
    # This file's own: today's tests give --host of a verb but init none.
    "host_name": (["", "nuc", "pve-2", "a" * 31], ["a" * 32, "Nuc", "-nuc", "nuc.lan", "nuc_2", "$(x)"]),
    # ONE_WORD's nuc; the rest this file's own.
    "current_host": (["nuc", "pve-2", "0", "a" * 31], ["", "a" * 32, "-nuc", "Nuc", "nuc.lan"]),
    # test_vm_size.py: the recipe's options, test_a_size_just_refuses_by_its_pattern and tools/check size.
    "cores": (["8", "2", "4", "999", ""], ["0", "-4", "1000", '8, "x": 1']),
    "memory": (["16G", "3072", "512M", "8G", ""], ["8T", "12345678"]),
    "disk": (["64G", "1T", ""], ["64", "064G", "1234567G"]),
    "repos": (
        # test_vm_repos.py: the recipe's option and tools/check repos. Twenty names of a hundred characters, 2019 in
        # all, are taken by the pattern and refused by tools/check's cap of 2000, a rule of the check's.
        [
            "one",
            "one,two.2,three-3",
            "_x,A9",
            "a",
            "",
            "one,two.2,_three-3",
            "a" * 100,
            ",".join(["a" * 100] * 20),
        ],
        ["owner/name", ".claude", "-x", "a,,b", ",a", "a,", "a b", "a" * 101, 'a", "b', "a\nb"],
    ),
    "roles": (
        # The roles given; and claude, common,always, common,,tailscale and the comma alone, which
        # test_tags_may_name_only_the_roles refuses by the rule that names the roles, not by the pattern.
        [
            "claude_code",
            "claude_code,tailscale",
            "github_projects",
            "common",
            "claude",
            "common,always",
            "common,,tailscale",
            ",",
        ],
        # test_tags_outside_their_characters_are_refused_by_the_pattern.
        ["claude_code;rm -rf x", "$(touch x)", "--skip-tags=common", "Common"],
    ),
    # DEPLOY_STAGES; test_an_option_is_checked_before_anything_runs.
    "stage": (["template", "provision", "configure", "ssh-config", ""], ["bogus", "check"]),
    # SIGN_INS; test_an_option_is_checked_before_anything_runs.
    "sign_in": (["claude", "github", "tailscale", ""], ["configure"]),
    # status --only vms,storage; test_status_refuses_a_section_it_has_not_got and the option's x.
    "sections": (["vms", "vms,storage", ""], ["x", "vms,,storage", "vms storage", "VMS", "access;id", ",vms"]),
    "project": (
        # test_code_passes_a_project_as_its_own_json_extra_var, the repo beside a session, and . and .., which
        # test_code_refuses_a_bad_project_before_the_playbook refuses by tools/check project's rule, not the pattern.
        ["discord-music-bot", "ParkBnb", "djangoTest", "omkar_kv", "site.github.io", "x", "a" * 100, "repo", ".", ".."],
        # test_code_refuses_a_bad_project_before_the_playbook; test_a_value_is_never_run_as_code.
        [
            "../etc",
            "org/repo",
            "/etc",
            "a b",
            'a"b',
            "a,b",
            "repo\nother",
            "répo",
            "a" * 101,
            "$(touch ran)",
            "`touch ran`",
        ],
    ),
    "session": (
        # test_code_passes_a_session_as_its_own_json_extra_var; tools/check session's none.
        [SESSION_ID, ""],
        # BAD_SESSIONS; test_a_value_is_never_run_as_code.
        [
            SESSION_ID.upper(),
            SESSION_ID + "0",
            SESSION_ID[:-1],
            SESSION_ID.replace("-", ""),
            "../" + SESSION_ID,
            SESSION_ID + "' -e 'x=1",
            SESSION_ID + '"}',
            "$(x)",
            SESSION_ID + "\nother",
            "-" * 36,
            "$(touch ran)",
            "`touch ran`",
        ],
    ),
    # The settings tests' names; the refused this file's own.
    "setting_name": (
        ["tailscale", "vm_cores", "vm_default_name", "tailscale_auth_key"],
        ["", "Vm_cores", "1x", "vm-cores", "-x"],
    ),
    # This file's own: no recipe takes a job's ID.
    "job_id": (
        ["0123456789abcdef", "9f3c2a1b7d4e6f80"],
        ["0123456789ABCDEF", "0123456789abcde", "0123456789abcdef0", "../0123456789abc", ""],
    ),
    # test_code_passes_an_editor_as_its_own_json_extra_var;
    # test_code_refuses_an_editor_it_cannot_open_before_the_playbook.
    "editor": (["vscode", "zed", ""], ["emacs", "VSCode", "vscode,zed", '"}', "$(x)", "-e"]),
    # test_just_lists_every_recipe_and_help_names_each_option, help with each recipe's name; the refused this file's
    # own.
    "verb": (
        ["help", "init", "install", "vault", "deploy", "code", "login", "status", "settings", "destroy", "host"]
        + ["logs", "mcp", "lint", "test", "docs", "vagrant", "clean"],
        ["", "Deploy", "ssh-config", "d1", "-x"],
    ),
    "text": (
        # test_settings_passes_a_value_as_one_argument_and_runs_none_of_it, the values of the settings tests, and the
        # empty value.
        ["$(touch ran) `touch ran` 'single' \"double\" ; | & spaces", "x", "8", "4", ""],
        # test_settings_refuses_a_control_character_in_a_value_before_anything_runs; the rest this file's own.
        ["8\n9", "a\tb", "a\rb", "\x7f", "\x00"],
    ),
    # This file's own.
    "path": (
        ["", "/", "/home/someone/.vault_pass", "~/projects/app"],
        ["relative/path", ".vault_pass", "/a\nb", "~/a\tb"],
    ),
    # This file's own.
    "attended": (["", "0", "1"], ["2", "yes", "01"]),
    # GIVEN, the secret file's tests, init --host's pve.lan, nuc and the empty value; OUTSIDE_INIT_S.
    "address": (["pve.lan", "pve.example", "nuc", ""], OUTSIDE_INIT_S),
    "proxmox_id": (["local-zfs", "vmbr1", "claude", ""], OUTSIDE_INIT_S),
    "token_user": (["deploy@pam", ""], OUTSIDE_INIT_S),
    "github_user": (["octo-cat", ""], OUTSIDE_INIT_S),
    "secret_file": (["~/.config/pve-token", "pve-token", ""], OUTSIDE_INIT_S),
    # GIVEN's off, the other host's on; test_tailscale_is_on_or_off.
    "on_off": (["on", "off", ""], ["yes"]),
    # This file's own: today's tests give --certificate none.
    "fingerprint": (
        [FINGERPRINT, ":".join(["0f"] * 32), ""],
        [
            ":".join(["AB"] * 31),
            "AB" * 32,
            FINGERPRINT + ":AB",
            "sha256 Fingerprint=" + FINGERPRINT,
            ":".join(["AG"] * 32),
        ],
    ),
    # This file's own, as for every pattern below: no recipe of the justfile takes these.
    "assignment": (
        ["vm_cores=4", "editor=zed", "github_projects=a,b", "vm_default_name=", "tailscale_hostname=$(x) `y`"],
        ["", "=4", "vm_cores", "Vm_cores=4", "vm-cores=4", "1x=4", "vm_cores=4\n5", "vm_cores=a\tb", "-e=1"],
    ),
    "secret_name": (
        ["token", "vm_password", "anthropic_api_key", "tailscale_oauth_client_secret"],
        ["", "Token", "1x", "vault-token", "-x", "a b", "token.x"],
    ),
    "ssh_fingerprint": (
        [SSH_FINGERPRINT, ""],
        [
            SSH_FINGERPRINT[:-1],
            SSH_FINGERPRINT + "A",
            SSH_FINGERPRINT + "=",
            SSH_FINGERPRINT.replace("SHA256", "sha256"),
            "MD5:" + SSH_FINGERPRINT[7:],
            SSH_FINGERPRINT[7:],
            SSH_FINGERPRINT[:-1] + "-",
        ],
    ),
    "release": (
        ["3.0.0", "v3.0.0", "v3.0.0-rc.1", "2.7.0", "v10.20.30", ""],
        ["3.0", "v3", "latest", "3.0.0-", "v3.0.0-RC.1", "V3.0.0", "../3.0.0", "3.0.0;x", "-3.0.0", "3.0.0.1"],
    ),
    "dependency_group": (["mcp", ""], ["dev", "docs", "MCP", "mcp,dev", "-x"]),
}

TAKEN = [
    pytest.param(name, value, id=f"{name}-{n}") for name, (taken, _) in CASES.items() for n, value in enumerate(taken)
]
REFUSED = [
    pytest.param(name, value, id=f"{name}-{n}")
    for name, (_, refused) in CASES.items()
    for n, value in enumerate(refused)
]


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_a_pattern_is_its_inner_text_with_no_anchor(name: str) -> None:
    text = PATTERNS[name].text
    assert not text.startswith(("^", r"\A")) and not text.endswith(("$", r"\Z")), text
    re.compile(text)


def test_every_pattern_has_values_it_takes_and_values_it_refuses() -> None:
    assert set(CASES) == set(PATTERNS)
    assert [name for name, (taken, refused) in CASES.items() if not taken or not refused] == []


@pytest.mark.parametrize(("name", "value"), TAKEN)
def test_a_pattern_takes_a_value_today_s_takes_and_never_with_a_newline_after_it(name: str, value: str) -> None:
    assert full(name, value), (name, value)
    assert not full(name, value + "\n"), (name, value)


@pytest.mark.parametrize(("name", "value"), REFUSED)
def test_a_pattern_refuses_a_value_today_s_refuses(name: str, value: str) -> None:
    assert not full(name, value), (name, value)


def test_full_holds_the_value_whole_as_experiment_31_asks() -> None:
    # Its clause on patterns, here a unit test: web against a pattern of letters, with nothing, a newline or a
    # carriage return after it.
    assert full("verb", "web") and not full("verb", "web\n") and not full("verb", "web\r")
    assert not full("verb", "\nweb") and not full("verb", "web\nweb")


def test_full_with_a_name_it_does_not_have_raises() -> None:
    with pytest.raises(KeyError, match="no pattern of the command line is named 'vm_nmae'"):
        full("vm_nmae", "alpha")


def test_full_compiles_a_pattern_once_on_its_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(patterns, "_COMPILED", {})
    assert full("cores", "8") and list(patterns._COMPILED) == ["cores"]
    first = patterns._COMPILED["cores"]
    assert not full("cores", "0") and patterns._COMPILED["cores"] is first and list(patterns._COMPILED) == ["cores"]


def test_the_patterns_import_nothing_outside_the_standard_library(home) -> None:
    # What importing the package alone loads - the interpreter's start, and anything a .pth file of the environment
    # imports there - is taken away: what is left is what the module itself brings.
    added = imports_of(module=MODULE, home=home) - imports_of(module="claude_vm", home=home)
    assert MODULE in added, sorted(added)
    tops = {name.split(".")[0] for name in added}
    assert sorted(tops - set(sys.stdlib_module_names) - {"claude_vm"}) == []


# ---------------------------------------------------------------- what a value may be, in words ----

# The examples patterns.py gives the two fingerprints, which no form shows: written here as there.
EXAMPLE_FINGERPRINT = ":".join(["AB", "CD", "EF", "01", "23", "45", "67", "89"] * 4)
EXAMPLE_SSH_FINGERPRINT = "SHA256:" + "Ab0+" * 10 + "xyz"
# A near miss of each example of each pattern, by the example: one character or one part away, and refused. Written
# here by hand, so that an example the pattern does not hold, or a pattern loosened past its words, fails.
NEAR_MISSES: dict[str, dict[str, str]] = {
    "vm_name": {"alpha": "-alpha", "dev-2.lab": "dev 2.lab"},
    "vm_names": {"alpha,beta": "alpha;beta", "alpha": "_alpha"},
    "host_name": {"lab2": "Lab2"},
    "current_host": {"lab2": "lab_2"},
    "cores": {"8": "08"},
    "memory": {"16G": "16GB", "2048": "2048.5"},
    "disk": {"128G": "128", "1T": "1TB"},
    "repos": {"my-app,my-lib": "my-app,-my-lib"},
    "roles": {"claude_code,dev_tools": "claude_code,dev-tools"},
    "stage": {"configure": "config", "ssh-config": "ssh_config"},
    "sign_in": {"github": "gh"},
    "sections": {"vms,work": "vms, work", "sign-ins": "signins"},
    "project": {"my-app": "my/app"},
    "session": {"0a608ad8-1b2c-4d3e-8f90-123456789abc": "0A608AD8-1B2C-4D3E-8F90-123456789ABC"},
    "setting_name": {"vm_cores": "vm-cores"},
    "job_id": {"3f9a0c1e7b2d4a58": "3f9a0c1e7b2d4a5"},
    "editor": {"zed": "Zed"},
    "verb": {"deploy": "Deploy"},
    "text": {"two words": "two\nlines"},
    "path": {"~/projects/app": "projects/app", "/srv/aw": "srv/aw"},
    "attended": {"1": "yes"},
    "address": {"pve.example": "https://pve.example", "192.0.2.10": "192.0.2.10/24"},
    "proxmox_id": {"local-lvm": "local lvm"},
    "token_user": {"ansible@pve": "ansible@pve!ansible"},
    "github_user": {"octocat": "octo_cat"},
    "secret_file": {"~/secrets/token": "~/my secrets/token"},
    "on_off": {"on": "yes"},
    "fingerprint": {EXAMPLE_FINGERPRINT: EXAMPLE_FINGERPRINT[:-3]},
    "assignment": {"vm_cores=8": "vm-cores=8"},
    "secret_name": {"github_token": "github-token"},
    "ssh_fingerprint": {EXAMPLE_SSH_FINGERPRINT: EXAMPLE_SSH_FINGERPRINT[:-1]},
    "release": {"v3.0.0": "v3.0", "3.0.0-rc.1": "3.0.0-RC.1"},
    "dependency_group": {"mcp": "dev"},
}
# What a pattern is written with, which no words may hold: a person reads the words, never a regular expression.
PATTERN_CHARACTERS = "[]{}\\|?*^$"
# What help and a refusal say for the owner's five rows of "Before and after" (OWNER-9.md 2), as worded here.
BEFORE_AND_AFTER = {
    "cores": "[a whole number from 1 to 999, e.g. 8]",
    "memory": "[a whole number of MiB, or a whole number with M or G after it, e.g. 16G]",
    "fingerprint": "[a SHA-256 fingerprint as openssl prints it: 32 pairs of hex digits joined by colons]",
    "stage": "[one of template, provision, configure, ssh-config]",
    "assignment": "[a setting's name, then = and its value for this run, e.g. vm_cores=8]",
}


def test_every_pattern_has_its_words_and_its_examples() -> None:
    lacking = [
        name
        for name, pattern in PATTERNS.items()
        if not isinstance(pattern.words, str)
        or not pattern.words.strip()
        or not isinstance(pattern.examples, tuple)
        or not pattern.examples
        or not all(isinstance(example, str) and example for example in pattern.examples)
        or type(pattern.shown) is not bool
    ]
    assert lacking == []


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_each_example_is_held_by_its_pattern_and_a_near_miss_of_each_is_not(name: str) -> None:
    assert set(NEAR_MISSES) == set(PATTERNS)
    examples = PATTERNS[name].examples
    assert tuple(NEAR_MISSES[name]) == examples, "a near miss for each example, in its order"
    assert [example for example in examples if not full(name, example)] == []
    assert [near for near in NEAR_MISSES[name].values() if full(name, near)] == []


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_no_words_hold_a_pattern_s_own_characters(name: str) -> None:
    words = PATTERNS[name].words
    assert [character for character in PATTERN_CHARACTERS if character in words] == [], words
    # The words are a clause the form ends with its example: no full stop, and no example of their own.
    assert not words.endswith(".") and "e.g." not in words and words == words.strip(), words


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_the_form_is_the_words_and_the_first_example_or_the_words_alone(name: str) -> None:
    pattern = PATTERNS[name]
    said = form(name)
    assert said == (f"[{pattern.words}, e.g. {pattern.examples[0]}]" if pattern.shown else f"[{pattern.words}]")
    assert pattern.text not in said and "pattern" not in said, said
    assert "\n" not in said


def test_the_owner_s_before_and_after_reads_as_written() -> None:
    assert {name: form(name) for name in BEFORE_AND_AFTER} == BEFORE_AND_AFTER


def test_form_with_a_name_it_does_not_have_raises() -> None:
    with pytest.raises(KeyError):
        form("vm_nmae")
