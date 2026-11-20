"""Today's command line, v2.7.0's, as a contract: tests/unit/cli_contract.json, and how each part of it is made again.

The file is what every parity test reads until the cutover, never the justfile: the parity suite holds the new
program to it, so that nothing a person types today is lost. tests/unit/test_cli_contract.py holds the file to the
justfile and tools/check of the tree it runs in. Both go with today's engine at the cutover.

Six parts, in this order:

- recipes: the thirteen a person runs (the group use, and help), in the justfile's order (its --summary --unsorted),
  each with its group, its [doc], the words it takes as the command reference prints them (tools/docs.py's usage), and
  its parameters in order: name, long and short option, flag value, patterns, help, whether it is the recipe's words
  and whether those are plural (lib/claude_vm/mcp/recipes.py's words()), and its default (just's, '' for every option
  today: not given). Read from its --json (harness.just_json). The developer's recipes (lint, test, docs, vagrant,
  clean) stay the task runner's own and are left out.
- settings: the five a release promises and the two read from the command line alone, and apart from them STATUS_DIR
  and LIVE_DIR, which the justfile also reads with env() but a release does not promise: each with its default (its
  --evaluate in an empty environment, this checkout written <checkout>) and its line.
- rules: every refusal the justfile says, and those of tools/check's word checks, by code (RULES below):
  <recipe>.<n>, check.<label>.<n>, and just.<label> for a sentence of the task runner's own; each with the sentence
  as written and its line. Every line of the use recipes' scripts, and of the six word checks, that exits with a
  status other than 0 is a rule's, or one of NOT_RULES (unruled_exits).
- cases: at least one runnable case for each rule, the exit statuses, and a setting given empty before the recipe
  (CASES below): the words typed (or, for a check no recipe's words reach, the check's own command line, from
  tools/check), whether a terminal is on standard input, what else the run was given, and the status and the sentence a
  run gave, measured. A setting's case has for its sentence what the run was seen to do (OBSERVED).
- examples: every example command line of docs/reference/commands.md, and every command test_commands.py's scan finds
  in the sources it lists, with its file. They are kept as held, the 1,175 the contract was first written with first,
  in their order: only --scan adds a line, after them, and none is ever dropped, so a page edited later leaves them as
  they are.
- exits: the statuses a person can rely on; the two a Ctrl-C gives are the ones their cases measured. One Ctrl-C while
  the playbook runs is ansible-playbook's own status for it (99), since the recipe ends in the playbook; before the
  playbook starts it is the script's own (130).

Made again with the project's Python:

    tools/env-run python tests/unit/cli_contract.py --write     # every part, keeping what the cases measured
    tools/env-run python tests/unit/cli_contract.py --measure   # the same, with every case run again
    tools/env-run python tests/unit/cli_contract.py --write --scan   # and the example lines a fresh scan finds added

--write runs no recipe: it asks the justfile for its --json, --summary and --evaluate, and reads files. --measure
runs each case's recipe against stand-ins, as the test does: run it where the tests run. A rule or a case is added to
RULES or CASES by hand; a code once written is never given to another (a rule taken out goes to RETIRED), and a
recipe's new rule takes its next number. The recipes' part of v2.7.0 can also be made by hand from the tag, git show
v2.7.0:justfile, read with its --json; no test reads a tag or main.

A parity test replays a case through measure() and CASES, which hold what the file does not: how each case is run.
The examples are command lines as the scan found them, prose after the command and the developer's recipes among them:
a reader takes what it needs from each.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONTRACT = HERE / "cli_contract.json"
JUSTFILE = REPO / "justfile"
CHECK = REPO / "tools" / "check"
JUST = shutil.which("just")
# The files of the contract itself, whose commands are none of the examples.
OWN = {"tests/unit/cli_contract.py", "tests/unit/test_cli_contract.py"}
PARTS = ("recipes", "settings", "rules", "cases", "examples", "exits")

# The settings the justfile reads with env() that a release promises, the two it reads from the command line alone,
# and the two it reads with env() that only move caches under .cache/.
SETTINGS = {
    "promised": ("PYTHON", "UV", "ANSIBLE_ARGS", "LOG_DIR", "LOG_RETENTION_DAYS"),
    "command_line": ("ATTENDED", "RUN_LOCK"),
    "unpromised": ("STATUS_DIR", "LIVE_DIR"),
}

# Every refusal, by its code: the recipe it is one of (None for a check's or the task runner's own), where it is said
# (justfile, tools/check, or just itself), and a piece of its sentence that is on one line of that file and no other.
# A code once written is never given to another; a recipe's new rule takes its next number.
RULES = [
    ("help.1", "help", "justfile", "error: just help shows one recipe's options at a time"),
    ("init.1", "init", "justfile", "error: just init takes only its options"),
    ("init.2", "init", "justfile", "--host names a host of this checkout's, and its address is --address"),
    ("init.3", "init", "justfile", "error: a host's name is lowercase letters"),
    ("init.4", "init", "justfile", "may contain only $4"),
    ("vault.1", "vault", "justfile", "missing. Run: just init${on:+"),
    ("deploy.1", "deploy", "justfile", "there is no terminal to ask which was meant"),
    ("deploy.2", "deploy", "justfile", "One stage runs at a time, given with --only"),
    ("deploy.3", "deploy", "justfile", "Nothing was run."),
    ("deploy.4", "deploy", "justfile", "--tags names roles separated by single commas"),
    ("deploy.5", "deploy", "justfile", "--tags may name only"),
    ("deploy.6", "deploy", "justfile", "--tags narrows configuration, which --only $stage does not run"),
    ("deploy.7", "deploy", "justfile", "--tags on its own configures the VMs named beside it"),
    ("deploy.8", "deploy", "justfile", "--check is a dry run of configuration"),
    ("deploy.9", "deploy", "justfile", "builds the template, which takes no VM names"),
    ("deploy.10", "deploy", "justfile", "--start starts a VM the run would leave stopped"),
    ("deploy.11", "deploy", "justfile", "--cores, --memory and --disk size a VM the run creates"),
    ("deploy.12", "deploy", "justfile", "--repos gives a VM its list as it is provisioned"),
    ("deploy.13", "deploy", "justfile", "a VM name belongs to one host"),
    ("code.1", "code", "justfile", "error: code opens one VM:"),
    ("code.2", "code", "justfile", "error: code opens one VM at a time"),
    ("settings.1", "settings", "justfile", "takes one role or variable, and --value its value"),
    ("settings.2", "settings", "justfile", "--all lists every setting: give it no role or variable"),
    ("settings.3", "settings", "justfile", "--value and --reset each change the one setting named"),
    ("settings.4", "settings", "justfile", "--value sets a value and --reset takes one out"),
    ("destroy.1", "destroy", "justfile", "--confirm answers for the VMs named beside it"),
    ("destroy.2", "destroy", "justfile", "but the VMs given are $names: nothing was deleted"),
    ("host.1", "host", "justfile", "takes one host's name (got:"),
    ("host.2", "host", "justfile", "--json lists the hosts: give it no name"),
    ("host.3", "host", "justfile", "--for and --host say which host a run would reach"),
    ("host.4", "host", "justfile", "--remove deletes the host named beside it"),
    ("host.5", "host", "justfile", "--remove asks you to type the host's name"),
    ("host.6", "host", "justfile", "Nothing was deleted."),
    ("mcp.1", "mcp", "justfile", "takes only its options (just help mcp), and nothing after them"),
    ("mcp.2", "mcp", "justfile", "--remove takes the server out and --show prints how it is added"),
    ("mcp.3", "mcp", "justfile", "--project-dir $project_dir is not a folder"),
    ("mcp.4", "mcp", "justfile", "--vault-password-file $vault_password_file is not a file"),
    ("mcp.5", "mcp", "justfile", "error: no claude on PATH"),
    ("mcp.6", "mcp", "justfile", "has no MCP SDK"),
    ("mcp.7", "mcp", "justfile", "is older than 2.1.309"),
    ("mcp.8", "mcp", "justfile", "the server runs only main with no tracked change"),
    ("mcp.9", "mcp", "justfile", "so a run the server starts, with no terminal, could not read it"),
    ("mcp.10", "mcp", "justfile", "claude mcp add said it added"),
    ("test.1", "test", "justfile", "--shard takes I/N with I no more than N"),
    ("test.2", "test", "justfile", "--shard splits the unit tests and --role narrows the role scenarios"),
    ("test.3", "test", "justfile", "--shard splits the unit tests, which $part is not"),
    ("test.4", "test", "justfile", "--role names roles separated by single commas"),
    ("test.5", "test", "justfile", "--role may name only"),
    ("test.6", "test", "justfile", "--role narrows the role scenarios, which $part does not run"),
    ("test.7", "test", "justfile", "--jobs runs Molecule scenarios side by side"),
    ("docs.1", "docs", "justfile", "takes one part, serve or build"),
    ("vagrant.1", "vagrant", "justfile", "takes one part, destroy"),
    ("clean.1", "clean", "justfile", "takes one part, logs or env"),
    ("clean.2", "clean", "justfile", "not this checkout's own folder: [$data]"),
    ("clean.3", "clean", "justfile", "not this checkout's own folder: [$legacy]"),
    ("check.names.1", None, "tools/check", "VM names start with a letter or a digit"),
    ("check.size.1", None, "tools/check", "--cores is a number of cores"),
    ("check.size.2", None, "tools/check", "--memory is MiB"),
    ("check.size.3", None, "tools/check", "--disk takes G or T"),
    ("check.repos.1", None, "tools/check", "--repos names GitHub repositories"),
    ("check.repos.2", None, "tools/check", "--repos is longer than the 2000 characters"),
    ("check.project.1", None, "tools/check", "--project may contain only"),
    ("check.project.2", None, "tools/check", "--project names one project folder"),
    ("check.project.3", None, "tools/check", "--project is longer than the 100 characters"),
    ("check.session.1", None, "tools/check", "--session is a Claude Code session ID"),
    ("check.sections.1", None, "tools/check", "--only takes the report's sections"),
    ("check.sections.2", None, "tools/check", "the report has no section"),
    ("just.unknown-option", None, "just", None),
    ("just.pattern", None, "just", None),
    ("just.repeated-option", None, "just", None),
]
# The codes of rules taken out of RULES, which no rule is given again.
RETIRED: list[str] = []

# The use recipes' scripts and the six word checks of tools/check, where every line that exits with a status other
# than 0 is a rule's: its sentence on that line or the one above it, or the said= that a line echoes. These are the
# lines that are not, each by a piece of it that is on one line of that file and no other, and why.
NOT_RULES = [
    ("justfile", "printf '%s\\n' \"$added\" >&2; exit 1", "claude mcp add's own words, printed as it said them"),
]
# The labels of tools/check whose refusals are rules here: the six word checks.
CHECKED = ("names", "size", "repos", "project", "session", "sections")


def case(code: str, *words: str, setup: str = "just", terminal: bool = False, given: str | None = None, **how) -> dict:
    """One runnable case: its code (its rule's with -<k>, or exit.<label>-<k> for an exit status), the words typed,
    how it is run (setup, and what that takes), whether a terminal is on standard input, and what else it is given."""
    return {"code": code, "words": list(words), "setup": setup, "terminal": terminal, "given": given, **how}


REPOS_TOO_LONG = "--repos=" + ",".join(["r" * 99] * 21)
CASES = [
    case("help.1-1", "help", "deploy", "login"),
    case("init.1-1", "init", "extra"),
    case("init.2-1", "init", "--host=old.example", "--address=new.example"),
    case("init.3-1", "init", "--host=Lab"),
    case(
        "init.4-1",
        "init",
        "--token-secret-file=secret",
        setup="space",
        given="started in a folder whose path has a space",
    ),
    case(
        "vault.1-1", "vault", settings=["VAULT_FILE=<tmp>/none.yml"], given="no vault file (VAULT_FILE=<tmp>/none.yml)"
    ),
    case("deploy.1-1", "deploy", "configure"),
    case("deploy.2-1", "deploy", "configure", "check", terminal=True, typed="s\n", given="s typed at the question"),
    case("deploy.3-1", "deploy", "configure", terminal=True, typed="\n", given="Enter typed at the question"),
    case("deploy.4-1", "deploy", "alpha", "--tags=common,,tailscale"),
    case("deploy.5-1", "deploy", "alpha", "--tags=claude"),
    case("deploy.6-1", "deploy", "--only=provision", "--tags=common"),
    case("deploy.7-1", "deploy", "--tags=claude_code"),
    case("deploy.8-1", "deploy", "--check", "--only=configure"),
    case("deploy.9-1", "deploy", "--only=template", "alpha"),
    case("deploy.10-1", "deploy", "--only=configure", "--start"),
    case("deploy.11-1", "deploy", "--only=configure", "--cores=8"),
    case("deploy.12-1", "deploy", "--only=configure", "--repos=my-app"),
    case("deploy.13-1", "deploy", "newvm", setup="hosts", given="a second host, nuc, which has a VM named newvm"),
    case("code.1-1", "code"),
    case("code.2-1", "code", "alpha", "beta"),
    case("settings.1-1", "settings", "vm_cores", "vm_memory_mb"),
    case("settings.2-1", "settings", "--all", "tailscale"),
    case("settings.3-1", "settings", "--value=4"),
    case("settings.4-1", "settings", "vm_cores", "--value=4", "--reset"),
    case("destroy.1-1", "destroy", "--confirm=alpha"),
    case("destroy.2-1", "destroy", "--confirm=alpha", "beta"),
    case("host.1-1", "host", "nuc", "lab"),
    case("host.2-1", "host", "--json", "nuc"),
    case("host.3-1", "host", "--for=alpha"),
    case("host.4-1", "host", "--remove"),
    case("host.5-1", "host", "nuc", "--remove"),
    case("host.6-1", "host", "nuc", "--remove", terminal=True, typed="\n", given="Enter typed at the question"),
    case("mcp.1-1", "mcp", "extra"),
    case("mcp.2-1", "mcp", "--remove", "--show"),
    case("mcp.3-1", "mcp", "--project-dir=<tmp>/none", given="no folder <tmp>/none"),
    case("mcp.4-1", "mcp", "--vault-password-file=<tmp>/none", given="no file <tmp>/none"),
    case("mcp.5-1", "mcp", setup="mcp", claude=None, given="no claude on PATH"),
    case("mcp.6-1", "mcp", setup="mcp", python="none", given="an environment whose Python has no MCP SDK"),
    case("mcp.7-1", "mcp", setup="mcp", claude="2.1.300", given="Claude Code 2.1.300 on PATH"),
    case("mcp.8-1", "mcp", setup="mcp", branch="topic", given="a checkout on a branch named topic"),
    case(
        "mcp.9-1",
        "mcp",
        "--any-branch",
        setup="mcp",
        vault_pass=False,
        given="an encrypted vault, and no vault password file (VAULT_PASS=<tmp>/vault_pass)",
    ),
    case(
        "mcp.10-1", "mcp", "--any-branch", setup="mcp", claude="lost", given="claude mcp get not finding what was added"
    ),
    case("test.1-1", "test", "--shard=3/2"),
    case("test.2-1", "test", "--shard=1/2", "--role=common"),
    case("test.3-1", "test", "role", "--shard=1/2"),
    case("test.4-1", "test", "--role=common,,dev_tools"),
    case("test.5-1", "test", "--role=claude"),
    case("test.6-1", "test", "unit", "--role=common"),
    case("test.7-1", "test", "unit", "--jobs=4"),
    case("docs.1-1", "docs", "serve", "build"),
    case("vagrant.1-1", "vagrant", "destroy", "destroy"),
    case("clean.1-1", "clean", "logs", "env"),
    case(
        "clean.2-1",
        "clean",
        "env",
        setup="elsewhere",
        folder="data",
        given="tools/env-run naming a folder that is no checkout's own (<tmp>/elsewhere): nothing else reaches it",
    ),
    case(
        "clean.3-1",
        "clean",
        "env",
        setup="elsewhere",
        folder="legacy",
        given="tools/env-run naming a 1.x folder that is no checkout's own (<tmp>/elsewhere): nothing else reaches it",
    ),
    case("check.names.1-1", "host", "--json", "--for=alpha,-b"),
    case(
        "check.size.1-1",
        "tools/check",
        "size",
        "0",
        "",
        "",
        setup="check",
        given="the check alone: --cores' pattern refuses 0 first",
    ),
    case(
        "check.size.2-1",
        "tools/check",
        "size",
        "",
        "16X",
        "",
        setup="check",
        given="the check alone: --memory's pattern refuses 16X first",
    ),
    case(
        "check.size.3-1",
        "tools/check",
        "size",
        "",
        "",
        "64",
        setup="check",
        given="the check alone: --disk's pattern refuses 64 first",
    ),
    case(
        "check.repos.1-1",
        "tools/check",
        "repos",
        "a,,b",
        setup="check",
        given="the check alone: --repos' pattern refuses a,,b first",
    ),
    case(
        "check.repos.1-2",
        "tools/check",
        "repos",
        ".a",
        setup="check",
        given="the check alone: --repos' pattern refuses .a first",
    ),
    case(
        "check.repos.1-3",
        "tools/check",
        "repos",
        "r" * 101,
        setup="check",
        given="the check alone: --repos' pattern refuses a name of 101 characters first",
    ),
    case("check.repos.2-1", "deploy", "alpha", REPOS_TOO_LONG, given="--repos of 21 names, 2099 characters"),
    case(
        "check.project.1-1",
        "tools/check",
        "project",
        "a/b",
        setup="check",
        given="the check alone: --project's pattern refuses a/b first",
    ),
    case("check.project.2-1", "code", "alpha", "--project=.."),
    case(
        "check.project.3-1",
        "tools/check",
        "project",
        "p" * 101,
        setup="check",
        given="the check alone: --project's pattern refuses 101 characters first",
    ),
    case(
        "check.session.1-1",
        "tools/check",
        "session",
        "not-a-session",
        setup="check",
        given="the check alone: --session's pattern refuses it first",
    ),
    case(
        "check.sections.1-1",
        "tools/check",
        "sections",
        "vms,,storage",
        setup="check",
        given="the check alone: --only's pattern refuses vms,,storage first",
    ),
    case(
        "check.sections.2-1",
        "tools/check",
        "sections",
        "disks",
        setup="check",
        given="the check alone: --only's pattern refuses disks first",
    ),
    case("just.unknown-option-1", "deploy", "--bogus"),
    case("just.pattern-1", "deploy", "--cores=0"),
    case("just.pattern-2", "deploy", "--only=check", given="a stage deploy has not got"),
    case("just.pattern-3", "destroy", "alpha;beta", given="a VM name outside its characters"),
    case("just.repeated-option-1", "deploy", "--cores=8", "--cores=4", "alpha", given="an option given twice"),
    case("just.repeated-option-2", "deploy", "--check", "--check", given="a flag given twice"),
    case(
        "exit.ctrl-c-1",
        "deploy",
        "alpha",
        setup="interrupt",
        terminal=True,
        given="one Ctrl-C (SIGINT to the run's process group) while ansible-playbook, a stand-in that does on it what "
        "ansible-playbook does (PLAYBOOK_INTERRUPTED), runs",
    ),
    case(
        "exit.ctrl-c-2",
        "deploy",
        "alpha",
        setup="interrupt-before",
        terminal=True,
        given="one Ctrl-C (SIGINT to the run's process group) before the playbook starts, while the last check reads "
        "the settings (<tmp>/local.yml) with the environment's Python, a stand-in that dies of it as Python does",
    ),
    case(
        "setting.run-lock-1",
        "RUN_LOCK=",
        "deploy",
        "alpha",
        setup="lock",
        given="a copy of the checkout whose own run lock (its .cache/run.lock) a live run holds",
    ),
    case(
        "setting.run-lock-2",
        "deploy",
        "alpha",
        setup="lock",
        given="a copy of the checkout whose own run lock (its .cache/run.lock) a live run holds: without RUN_LOCK=",
    ),
    case("exit.report-ok-1", "status", setup="report", rc=0, given="the report's engine, a stand-in, exits 0"),
    case("exit.report-problem-1", "status", setup="report", rc=1, given="the report's engine, a stand-in, exits 1"),
    case("exit.report-unread-1", "status", setup="report", rc=2, given="the report's engine, a stand-in, exits 2"),
    case("exit.report-failed-1", "status", setup="report", rc=3, given="the report's engine, a stand-in, exits 3"),
]


# ------------------------------------------------------------------------------------------------- the recipes ----


def _just(*args: str, justfile: Path = JUSTFILE) -> str:
    """The task runner on a justfile, in an environment of nothing but PATH and HOME: no PYTHON, no ANSIBLE_ARGS."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/")}
    return subprocess.run(
        [JUST, "--justfile", str(justfile), *args],
        cwd=justfile.parent,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout


def dump(justfile: Path = JUSTFILE) -> tuple[dict, list[str]]:
    """A justfile's --json, and its recipes in its own order."""
    if justfile == JUSTFILE:
        from harness import just_json

        data = just_json()
    else:
        data = json.loads(_just("--json", justfile=justfile))
    return data, _just("--summary", "--unsorted", justfile=justfile).split()


def _attribute(recipe: dict, name: str):
    return next(
        (attribute[name] for attribute in recipe["attributes"] if isinstance(attribute, dict) and name in attribute),
        None,
    )


@functools.cache
def _docs():
    from harness import load_module

    return load_module("tools/docs.py", "docs_generator", register=False)


def _words(name: str, recipe: dict) -> str | None:
    """The words a recipe takes, as the command reference prints them after its name: [<name>...], <name>."""
    usage = _docs()._usage(name, recipe).removeprefix(f"just {name}").strip()
    return usage or None


def _option(parameter: dict) -> dict:
    words = parameter["kind"] in ("star", "plus")
    return {
        "name": parameter["name"],
        "long": parameter["long"],
        "short": parameter["short"],
        "value": parameter["value"],
        "patterns": parameter["pattern"] or None,
        "help": parameter["help"],
        "words": words,
        "plural": words and parameter["name"].endswith("S"),
        "default": parameter["default"],
    }


def recipes(justfile: Path = JUSTFILE) -> list[dict]:
    """The recipes a person runs, in the justfile's order: the group use, and help."""
    data, order = dump(justfile)
    out = []
    for name in order:
        recipe = data["recipes"][name]
        group = _attribute(recipe, "group")
        if recipe["private"] or (group != "use" and name != "help"):
            continue
        out.append(
            {
                "name": name,
                "group": group,
                "doc": _attribute(recipe, "doc") or recipe["doc"],
                "words": _words(name, recipe),
                "options": [_option(parameter) for parameter in recipe["parameters"]],
            }
        )
    return out


def _spelled(option: dict) -> str:
    return f"--{option['long']}" if option["long"] else f"<{option['name']}>"


def differences(contract: list[dict], justfile: list[dict]) -> list[str]:
    """Where the recipes the contract holds and the justfile's differ, each said by its recipe and its option."""
    said = []
    names = [recipe["name"] for recipe in justfile]
    held = [recipe["name"] for recipe in contract]
    if names != held:
        said.append(f"the recipes: the justfile has {' '.join(names)}, the contract {' '.join(held)}")
    contract_by = {recipe["name"]: recipe for recipe in contract}
    for recipe in justfile:
        name = recipe["name"]
        old = contract_by.get(name)
        if old is None:
            continue
        for field in ("group", "doc", "words"):
            if old[field] != recipe[field]:
                said.append(f"{name}: its {field} is {recipe[field]!r} in the justfile, {old[field]!r} in the contract")
        options = {option["name"]: option for option in old["options"]}
        order = [option["name"] for option in recipe["options"]]
        if order != [option["name"] for option in old["options"]] and set(order) == set(options):
            said.append(f"{name}: its parameters are in another order: {' '.join(order)}")
        for option in recipe["options"]:
            before = options.get(option["name"])
            if before is None:
                said.append(f"{name} {_spelled(option)}: in the justfile, not in the contract")
                continue
            for field, value in option.items():
                if before[field] != value:
                    said.append(
                        f"{name} {_spelled(option)}: its {field} is {value!r} in the justfile, {before[field]!r} in "
                        "the contract"
                    )
        for gone in sorted(set(options) - set(order)):
            said.append(f"{name} {_spelled(options[gone])}: in the contract, not in the justfile")
    return said


# ------------------------------------------------------------------------------------------------ the settings ----


def _assigned_at(lines: list[str], name: str) -> int:
    (number,) = [at for at, line in enumerate(lines, 1) if line.startswith(f"{name} := ")]
    return number


def _evaluate(name: str) -> str:
    value = _just("--evaluate", name)
    for spelling in sorted({str(REPO), str(REPO.resolve())}, key=len, reverse=True):
        value = value.replace(spelling, "<checkout>")
    return value


def settings() -> dict:
    lines = JUSTFILE.read_text().splitlines()
    return {
        part: [{"name": name, "default": _evaluate(name), "line": _assigned_at(lines, name)} for name in names]
        for part, names in SETTINGS.items()
    }


# --------------------------------------------------------------------------------------------------- the rules ----


def _lines(where: str) -> list[str]:
    return (REPO / where).read_text().splitlines()


def _written(line: str, piece: str) -> str:
    """The sentence a line says as it is written there: the quoted text of its echo (or said=) that holds piece, a
    command substitution inside it included."""
    at = line.index(piece)
    opening = [m for m in re.finditer(r'(?:echo |said=)(\\?")', line) if m.end() <= at]
    if not opening:
        raise ValueError(f"no echo before {piece!r} in: {line.strip()}")
    start = opening[-1]
    i = start.end()
    if start.group(1) == '\\"':
        return line[i : line.index('\\"', i)]
    depth, j = 0, i
    while j < len(line):
        if line.startswith("$(", j):
            depth, j = depth + 1, j + 2
            continue
        if line[j] == "\\":
            j += 2
            continue
        if line[j] == ")" and depth:
            depth -= 1
        elif line[j] == '"' and not depth:
            return line[i:j]
        j += 1
    raise ValueError(f"no end to the sentence holding {piece!r} in: {line.strip()}")


def _one_line(where: str, piece: str, lines: list[str]) -> tuple[int, str]:
    found = [(at, line) for at, line in enumerate(lines, 1) if piece in line]
    if len(found) != 1:
        raise ValueError(
            f"{where}: {piece!r} is on {len(found)} lines, where it must be on one: where its words changed, change "
            f"its piece in RULES or NOT_RULES (tests/unit/cli_contract.py) to the new words, then --write"
        )
    return found[0]


def rule_at(where: str, piece: str) -> tuple[int, str]:
    """The line a rule's sentence is on, and the sentence as written there."""
    number, line = _one_line(where, piece, _lines(where))
    return number, _written(line, piece)


def _rule_of(code: str) -> str:
    """A case's rule: its code without the -<k> of the case."""
    return code.rsplit("-", 1)[0]


def rules(cases: list[dict]) -> list[dict]:
    """Every rule with its sentence and its line; the task runner's own say what their first case measured."""
    measured = {_rule_of(item["code"]): item["sentence"] for item in reversed(cases)}
    out = []
    for code, recipe, where, piece in RULES:
        if where == "just":
            line, sentence = None, measured.get(code)
        else:
            line, sentence = rule_at(where, piece)
        out.append({"code": code, "recipe": recipe, "sentence": sentence, "line": line, "by": where})
    return out


# A line that exits with a status other than 0.
EXITS_NONZERO = re.compile(r"\bexit [1-9][0-9]*\b")
NAME = r"[A-Za-z_][A-Za-z0-9_]*"


def _scripts(lines: list[str], recipes: list[str]) -> set[int]:
    """The lines of these recipes' scripts in a justfile's lines, by number from 1, with the line of every assignment
    a script interpolates and of each assignment that one names, all the way down."""
    assigned = {m.group(1): at for at, line in enumerate(lines, 1) if (m := re.match(rf"({NAME}) := ", line))}
    found: set[int] = set()
    names: list[str] = []
    for recipe in recipes:
        (header,) = [at for at, line in enumerate(lines, 1) if re.fullmatch(rf"{re.escape(recipe)}(?: [^:]*)?:", line)]
        at = header + 1
        while at <= len(lines) and (not lines[at - 1] or lines[at - 1][0] in " \t"):
            found.add(at)
            for interpolated in re.findall(r"\{\{(.*?)\}\}", lines[at - 1]):
                names += re.findall(NAME, interpolated)
            at += 1
    while names:
        name = names.pop()
        if name in assigned and assigned[name] not in found:
            found.add(assigned[name])
            value = lines[assigned[name] - 1].split(" := ", 1)[1]
            names += re.findall(NAME, re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "", value))
    return found


def _branches(lines: list[str], labels: tuple[str, ...]) -> set[int]:
    """The lines of these branches of tools/check's case, by number from 1: from the label to its ;;."""
    found: set[int] = set()
    for label in labels:
        start = lines.index(f"{label})") + 1
        end = lines.index("  ;;", start) + 1
        found |= set(range(start, end + 1))
    return found


def _said_by(lines: list[str], at: int) -> int | None:
    """The line whose sentence an exit at line at ends: that line, or the one above it, where it writes to standard
    error; for an echo of one variable, the line that set it, above."""
    said = next((n for n in (at, at - 1) if n >= 1 and ">&2" in lines[n - 1]), None)
    if said is None:
        return None
    echoed = re.search(rf'echo "\$({NAME})" >&2', lines[said - 1])
    if echoed:
        return next((n for n in range(said - 1, 0, -1) if re.match(rf"\s*{echoed.group(1)}=", lines[n - 1])), None)
    return said


def unruled_exits(where: str, recipes: list[str], text: str | None = None) -> list[tuple[int, str]]:
    """Each line that exits with a status other than 0, in the scripts of these recipes (where is the justfile) or in
    the six word checks (tools/check), whose sentence is no rule's and which is none of NOT_RULES: its number and its
    text. The rules' and NOT_RULES' lines are found in the text by their pieces, so a line moved is still theirs."""
    lines = (text if text is not None else (REPO / where).read_text()).splitlines()
    scanned = _scripts(lines, recipes) if where == "justfile" else _branches(lines, CHECKED)
    ruled = {_one_line(where, piece, lines)[0] for _, _, by, piece in RULES if by == where}
    known = {_one_line(where, piece, lines)[0] for by, piece, _ in NOT_RULES if by == where}
    return [
        (at, lines[at - 1].strip())
        for at in sorted(scanned)
        if EXITS_NONZERO.search(lines[at - 1]) and at not in known and _said_by(lines, at) not in ruled
    ]


# --------------------------------------------------------------------------------------------------- the cases ----


def pattern_of(sentence: str) -> re.Pattern:
    """A sentence as written made a pattern for what is printed: each $name, ${...}, $(...) and {{ ... }} in it any
    text, the rest itself, to the end of a line."""
    out, literal, i = [], [], 0

    def closing(i: int, opening: str, closing: str) -> int:
        depth = 0
        while i < len(sentence):
            if sentence[i] == opening:
                depth += 1
            elif sentence[i] == closing:
                depth -= 1
                if not depth:
                    return i + 1
            i += 1
        raise ValueError(f"unclosed {opening} in {sentence!r}")

    while i < len(sentence):
        rest = sentence[i:]
        if rest.startswith("{{"):
            end = sentence.index("}}", i) + 2
        elif rest.startswith("$("):
            end = closing(i + 1, "(", ")")
        elif rest.startswith("${"):
            end = closing(i + 1, "{", "}")
        elif re.match(r"\$[0-9*#@?$]", rest):
            end = i + 2
        elif re.match(r"\$[A-Za-z_]", rest):
            end = i + 1 + re.match(r"[A-Za-z_][A-Za-z0-9_]*", rest[1:]).end()
        else:
            literal.append(sentence[i])
            i += 1
            continue
        out += [re.escape("".join(literal)), "(.*?)"]
        literal = []
        i = end
    out.append(re.escape("".join(literal)))
    return re.compile("".join(out) + r"\Z")


def said(sentence: str | None, by: str, output: str) -> str | None:
    """What a run printed of a rule's sentence: the text its pattern matches on a line, or for the task runner's own,
    its first line that starts error:."""
    lines = output.splitlines()
    if by == "just":
        return next((line for line in lines if line.startswith("error:")), None)
    pattern = pattern_of(sentence)
    for line in lines:
        m = pattern.search(line)
        if m:
            return m.group(0)
    return None


def _anonymous(text: str, tmp: Path) -> str:
    """What a run printed, with its folder written <tmp> and this checkout <checkout>."""
    for spelling, name in sorted(
        {
            (str(tmp.resolve()), "<tmp>"),
            (str(tmp), "<tmp>"),
            (str(REPO.resolve()), "<checkout>"),
            (str(REPO), "<checkout>"),
        },
        key=lambda pair: len(pair[0]),
        reverse=True,
    ):
        text = text.replace(spelling, name)
    return text


def _given(values, tmp: Path) -> list[str]:
    return [str(value).replace("<tmp>", str(tmp)) for value in values]


def _plain(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """A recipe run as test_justfile.py runs one: its tools stand-ins, nothing of this checkout's own touched."""
    import test_justfile as tj

    settings = [*tj._recording(tmp), *_given(item.get("settings", ()), tmp)]
    if item["terminal"]:
        with tj._terminal_stdin(item.get("typed", "").encode()) as stdin:
            result = tj._just(*words, settings=settings, stdin=stdin)
    else:
        result = tj._just(*words, settings=settings)
    return result.returncode, result.stderr + result.stdout


def _space(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """Started in a folder whose path has a space, the justfile named."""
    import test_justfile as tj

    folder = tmp / "a b"
    folder.mkdir()
    result = tj._just(f"--justfile={JUSTFILE}", *words, settings=tj._recording(tmp), cwd=folder)
    return result.returncode, result.stderr + result.stdout


def _hosts(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """A copy of the checkout with a second host, nuc, whose VMs the report's engine (a stand-in) says."""
    import test_hosts as th

    root = th._checkout_copy(tmp)
    th._host_copy(root, "nuc")
    th._status_says(tmp, "taken newvm nuc")
    result = th._just_here(root, *words)
    return result.returncode, result.stderr + result.stdout


def _copy_on_branch(tmp: Path, branch: str) -> Path:
    """The justfile and tools/ in a git repository of their own, on branch, one commit."""
    from harness import GIT_ENV

    root = tmp / "checkout"
    root.mkdir()
    shutil.copy2(JUSTFILE, root / "justfile")
    shutil.copytree(REPO / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q", "-b", branch], ["add", "-A"], ["commit", "-q", "-m", "copy"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return root


def _mcp(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """just mcp with its tools stand-ins and a PATH of the system's directories: claude a stand-in or none, never the
    one this machine has; a vault that only looks encrypted."""
    import test_justfile as tj
    import test_mcp_register as tm

    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    if item.get("python") == "none":
        python.write_text("#!/bin/sh\nexit 1\n")
    else:
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    claude = item.get("claude", "2.1.319")
    if claude is not None:
        tm._claude(bin_dir, version="2.1.319" if claude == "lost" else claude, keeps=claude != "lost")
    vault = tmp / "vault.yml"
    vault.write_text("$ANSIBLE_VAULT;1.1;AES256\n3132\n")
    if item.get("vault_pass", True):
        (tmp / "vault_pass").write_text("x\n")
    settings = [f"BIN={bin_dir}", f"VAULT_FILE={vault}", f"VAULT_PASS={tmp / 'vault_pass'}"]
    path = os.pathsep.join([*filter(None, [tj._bash_dir()]), str(bin_dir), "/usr/bin", "/bin"])
    env = {"PATH": path, "HOME": str(tmp), "LANG": "en_US.UTF-8"}
    root = REPO
    if item.get("branch"):
        root = _copy_on_branch(tmp, item["branch"])
        words = [f"--justfile={root / 'justfile'}", *words]
    result = tj._just(*words, settings=settings, env=env, cwd=root)
    return result.returncode, result.stderr + result.stdout


def _elsewhere(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """A copy of the justfile whose tools/env-run names a folder that is no checkout's own: as data, or as the 1.x
    folder beside a good one."""
    import test_justfile as tj

    root = tmp / "copy"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(JUSTFILE, root / "justfile")
    shutil.copy2(CHECK, root / "tools" / "check")
    good = tmp / "data" / "agent-warehouse" / "checkouts" / "copy-1"
    good_legacy = tmp / "data" / "claude-on-proxmox" / "checkouts" / "copy-1"
    data, legacy = (tmp / "elsewhere", good_legacy) if item["folder"] == "data" else (good, tmp / "elsewhere")
    env_run = root / "tools" / "env-run"
    env_run.write_text(
        f'#!/bin/sh\ncase "$1" in --data) echo "{data}" ;; --legacy-data) echo "{legacy}" ;; *) exit 1 ;; esac\n'
    )
    env_run.chmod(0o755)
    result = tj._just(f"--justfile={root / 'justfile'}", *words, cwd=root)
    return result.returncode, result.stderr + result.stdout


def _check(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """tools/check run alone, as a recipe runs it: for a value no recipe's words reach it with."""
    result = subprocess.run(
        [str(CHECK), *words[1:]],
        cwd=REPO,
        env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stderr + result.stdout


# What ansible-playbook does on one Ctrl-C (ansible-core's CLI.cli_executor, on KeyboardInterrupt): it says so through
# its display, as an error, and exits ExitCode.KEYBOARD_INTERRUPT - once it has ended its workers, a moment after the
# signal, so that just, which waits for a recipe under [continue], exits as the playbook does. test_cli_contract.py
# holds the stand-in below to ansible-core's own.
PLAYBOOK_INTERRUPTED = (99, "[ERROR]: User interrupted execution")


def playbook_interrupted(started: Path) -> str:
    """The body of a stand-in ansible-playbook that marks started and waits, and on one Ctrl-C does what
    ansible-playbook does: a second later, says what it says and exits its status. A stand-in that ended within a
    millisecond of the signal would race just's own reading of it, and just would exit 130 whatever it exited."""
    status, says = PLAYBOOK_INTERRUPTED
    return f'trap \'sleep 1; echo "{says}" >&2; exit {status}\' INT\ntouch "{started}"\nsleep 20'


def _interrupt(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """One Ctrl-C to the run, at a terminal of its own, while ansible-playbook (a stand-in that does on it what
    ansible-playbook does) runs."""
    import test_justfile as tj

    started = tmp / "started"
    settings = tj._recording(tmp, playbook_body=playbook_interrupted(started))
    return tj._interrupted(started, *settings, *words)


def _interrupt_before(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """One Ctrl-C to the run, at a terminal of its own, before its playbook starts: while the last check before it,
    tools/check settings, reads <tmp>/local.yml with the environment's Python - a stand-in that waits there and, with no
    handler for the signal, dies of it as a Python program does."""
    import test_justfile as tj

    started = tmp / "started"
    settings = tj._recording(tmp)
    (tmp / "local.yml").write_text("{}\n")
    python = tmp / "bin" / "python"
    python.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = -I ] && [ "$2" = - ]; then touch "{started}"; exec sleep 20; fi\n'
        f'exec "{sys.executable}" "$@"\n'
    )
    return tj._interrupted(started, *settings, *words)


# A setting's case says what the run was seen to do, on a line of its own that starts so: its sentence in the file.
OBSERVED = "observed: "


def _lock(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """A copy of the checkout whose own run lock, its .cache/run.lock, a live run holds: just run there as typed, so
    that the lock is the one a run there takes unless the words give RUN_LOCK=. The holder is a process of its own,
    never an ancestor of the run, which would make the run the holder's own. Says whether the playbook ran, and whether
    the lock is as it was."""
    import test_hosts as th
    import test_justfile as tj
    import test_run_lock as tr

    root = th._checkout_copy(tmp)
    lock = root / ".cache" / "run.lock"
    lock.parent.mkdir(exist_ok=True)
    holder = subprocess.Popen(["sleep", "120"])
    try:
        tr._record(lock, holder.pid, tr._started(holder.pid), "deploy beta")
        held = lock.read_bytes()
        result = subprocess.run(
            [JUST, f"GIT_HOOK={tj._hooks_dir()}/pre-commit", *tj._recording(tmp), *words],
            cwd=root,
            env=tj._env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        kept = "as it was" if lock.read_bytes() == held else "changed"
    finally:
        holder.kill()
        holder.wait()
    ran = "ran" if tj._runs(tmp, "ansible-playbook") else "did not run"
    seen = f"{OBSERVED}the playbook {ran}, and the lock another run holds is {kept}"
    return result.returncode, f"{result.stderr}{result.stdout}{seen}\n"


def _report(item: dict, words: list[str], tmp: Path) -> tuple[int, str]:
    """just status over its engine, a stand-in that exits with rc."""
    import test_justfile as tj

    settings = tj._recording(tmp)
    (tmp / "status-rc").write_text(f"{item['rc']}\n")
    result = tj._just(*words, settings=settings)
    return result.returncode, result.stderr + result.stdout


SETUPS = {
    "just": _plain,
    "space": _space,
    "hosts": _hosts,
    "mcp": _mcp,
    "elsewhere": _elsewhere,
    "check": _check,
    "interrupt": _interrupt,
    "interrupt-before": _interrupt_before,
    "lock": _lock,
    "report": _report,
}


def observed(output: str) -> str | None:
    """What a setup saw the run do, where it says so (OBSERVED); None for every other case."""
    seen = [line.removeprefix(OBSERVED) for line in output.splitlines() if line.startswith(OBSERVED)]
    return seen[-1] if seen else None


def measure(item: dict, tmp: Path) -> tuple[int, str | None, str]:
    """Run one case in tmp: its exit status, the sentence of its rule it printed (for a setting's case what the run was
    seen to do, and None for an exit status's), and everything it printed, tmp written <tmp>."""
    rule = {code: (sentence, by) for code, _, by, sentence in _rule_rows()}.get(_rule_of(item["code"]))
    status, output = SETUPS[item["setup"]](item, _given(item["words"], tmp), tmp)
    output = _anonymous(output, tmp)
    sentence = said(rule[0], rule[1], output) if rule else observed(output)
    return status, sentence, output


def _rule_rows() -> list[tuple[str, str | None, str, str | None]]:
    """Each rule's code, its recipe, where it is said and its sentence as written (None for the task runner's own)."""
    return [
        (code, recipe, where, None if where == "just" else rule_at(where, piece)[1])
        for code, recipe, where, piece in RULES
    ]


def cases(kept: dict[str, dict], measured: dict[str, tuple[int, str | None]] | None = None) -> list[dict]:
    """Every case as the file holds it: from CASES, and its status and sentence as measured, or as kept."""
    lines = {code: line for code, _, where, piece in RULES if where != "just" for line in [rule_at(where, piece)[0]]}
    out = []
    for item in CASES:
        code = item["code"]
        status, sentence = (measured or {}).get(code) or (
            kept.get(code, {}).get("status"),
            kept.get(code, {}).get("sentence"),
        )
        out.append(
            {
                "code": code,
                "words": item["words"],
                "terminal": item["terminal"],
                "status": status,
                "sentence": sentence,
                "line": lines.get(_rule_of(code)),
                "given": item["given"],
            }
        )
    return out


# ------------------------------------------------------------------------------------------------ the examples ----


def _reference() -> list[str]:
    """docs/reference/commands.md's example command lines: each code span of its prose that is a command, joined where
    it breaks over a line, and each command with an option that its generated tables give in brackets."""
    text = (REPO / "docs" / "reference" / "commands.md").read_text()
    prose, _, tables = text.partition("<!-- BEGIN_GENERATED: commands -->")
    found = []
    for span in re.findall(r"`([^`]+)`", prose):
        line = " ".join(span.split())
        if re.match(r"(?:[A-Z][A-Z_]*=(?:'[^']*'|\S+) )*just [a-z]", line):
            found.append(line)
    found += [" ".join(m.split()) for m in re.findall(r"\((just [a-z][^)]* --[^)]*)\)", tables)]
    return found


@functools.cache
def _setting() -> str:
    """test_commands.py's pattern of a setting before the recipe, which also takes an empty value, as RUN_LOCK= is
    given: its scan's blind spot, which stays the 2.x test's own."""
    import test_commands as tc

    empty = tc.SETTING.replace(r"[^\s`'\"]+)", r"[^\s`'\"]*)")
    if empty == tc.SETTING:
        raise ValueError("test_commands.SETTING changed: take its unquoted value empty here again")
    return empty


@functools.cache
def command() -> re.Pattern:
    """test_commands.py's pattern of a just command, a setting before the recipe allowed to be empty."""
    import test_commands as tc

    return re.compile(tc.COMMAND.pattern.replace(tc.SETTING, _setting()))


def matches(text: str):
    """Each just command in text, as test_commands.py's _matches gives one, a setting given empty among them: the file
    (none), its line, the settings before the recipe, the recipe, and its words."""
    import test_commands as tc

    text = tc._normalized(text)
    for match in command().finditer(text):
        line = text[: match.start()].count("\n") + 1
        settings = re.findall(rf"(?<!\S){_setting()}", match.group(1))
        yield "", line, [s.split("=", 1)[0] for s in settings], match.group(2), match.group(3).split()


def scan() -> list[dict]:
    """A fresh scan: the reference's examples, then every command test_commands.py's scan finds in its sources, a
    setting given empty before the recipe among them: each command line once, with the first file it was found in."""
    import test_commands as tc
    from harness import public_recipes

    found = [{"line": line, "source": "docs/reference/commands.md"} for line in _reference()]
    for source, text in tc._sources(tc.REFUSED_ON_PURPOSE | OWN):
        for match in command().finditer(tc._normalized(text)):
            if match.group(2) in public_recipes():
                found.append({"line": " ".join(match.group(0).split()), "source": source})
    return examples([], found)


def examples(held: list[dict], found: list[dict] | None = None) -> list[dict]:
    """The examples as held; and given found, a fresh scan's (--scan), each line of it not held yet, after them. None is
    ever dropped, and a line is held once."""
    seen, out = set(), []
    for example in [*held, *(found or [])]:
        if example["line"] not in seen:
            seen.add(example["line"])
            out.append(example)
    return out


# --------------------------------------------------------------------------------------------------- the exits ----

PLAYBOOK_RECIPES = ["init", "deploy", "code", "login", "destroy"]


def exits(all_cases: list[dict]) -> list[dict]:
    """The exit statuses a person can rely on: a Ctrl-C's as its case measured it."""
    status = {item["code"]: item["status"] for item in all_cases}
    names = [recipe["name"] for recipe in recipes()]
    return [
        {"status": 0, "meaning": "the run did what was asked, or there was nothing to do", "recipes": names},
        {
            "status": 1,
            "meaning": "refused before anything ran: a rule between options, a value outside its pattern or an option "
            "the recipe has not got (the task runner's own), or a check before the work",
            "recipes": names,
        },
        {"status": 0, "meaning": "the report: nothing is wrong", "recipes": ["status"]},
        {"status": 1, "meaning": "the report: a problem", "recipes": ["status"]},
        {"status": 2, "meaning": "the report: something could not be read", "recipes": ["status"]},
        {"status": 3, "meaning": "the report: the run itself failed", "recipes": ["status"]},
        {
            "status": "the playbook's",
            "meaning": "a run that reached its playbook exits with ansible-playbook's status: the recipe ends in it",
            "recipes": PLAYBOOK_RECIPES,
        },
        {
            "status": status.get("exit.ctrl-c-1"),
            "meaning": "one Ctrl-C while the playbook runs: ansible-playbook's own status for it, which the recipe "
            "ends in (case exit.ctrl-c-1). aw's 130 for a cancelled run is a change against it, which "
            "tests/unit/cli_contract_changes.py's CHANGED names",
            "recipes": ["deploy"],
        },
        {
            "status": status.get("exit.ctrl-c-2"),
            "meaning": "one Ctrl-C before the playbook starts, while a check runs: the recipe's script ends of it "
            "(case exit.ctrl-c-2)",
            "recipes": ["deploy"],
        },
    ]


# ----------------------------------------------------------------------------------------------------- the file ----


def load() -> dict:
    return json.loads(CONTRACT.read_text()) if CONTRACT.exists() else {}


def build(kept: dict, measured: dict[str, tuple[int, str | None]] | None = None, scanned: bool = False) -> dict:
    """The whole contract: every part made again, the cases' status and sentence as measured or as kept, and the
    examples as kept, with what a fresh scan finds added only where scanned."""
    kept_cases = {item["code"]: item for item in kept.get("cases", [])}
    all_cases = cases(kept_cases, measured)
    return {
        "recipes": recipes(),
        "settings": settings(),
        "rules": rules(all_cases),
        "cases": all_cases,
        "examples": examples(kept.get("examples", []), scan() if scanned else None),
        "exits": exits(all_cases),
    }


def text(contract: dict) -> str:
    """The file's text: recipes and settings indented, each rule, case, example and exit on a line of its own."""
    parts = []
    for key, value in contract.items():
        if key in ("rules", "cases", "examples", "exits"):
            body = "[\n" + ",\n".join(f"    {json.dumps(item, ensure_ascii=False)}" for item in value) + "\n  ]"
        else:
            body = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n  ")
        parts.append(f"  {json.dumps(key)}: {body}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="make every part again, keeping what the cases measured")
    parser.add_argument("--measure", action="store_true", help="make every part again, every case run again")
    parser.add_argument(
        "--scan", action="store_true", help="with either: add the example lines a fresh scan finds, after those held"
    )
    args = parser.parse_args()
    if not (args.write or args.measure):
        parser.error("give --write or --measure")
    measured = None
    if args.measure:
        measured = {}
        for item in CASES:
            with tempfile.TemporaryDirectory(prefix="contract-") as name:
                status, sentence, _ = measure(item, Path(name))
            measured[item["code"]] = (status, sentence)
            print(f"{item['code']}: {status} {sentence or ''}".rstrip())
    CONTRACT.write_text(text(build(load(), measured, scanned=args.scan)))
    print(f"wrote {CONTRACT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.path[:0] = [str(HERE), str(REPO / "lib")]
    sys.exit(main())
