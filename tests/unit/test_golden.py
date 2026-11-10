"""A golden template's image entry points, its toolchain digest and key (docs/architecture/custom-code.md §7.13).

The static guard is the reason a golden can be shared by every VM of one key: everything an image entry point does reads
only that entry point's options, whose values the key holds, and the OS facts, which the base's identity covers. A
name a VM is told apart by, a secret, or a lookup of this machine's files would make one VM's image another's."""

from __future__ import annotations

import json
import re
import shutil
from functools import cache
from pathlib import Path

import jinja2
import jinja2.meta
import jinja2.nodes
import pytest
import yaml
from harness import REPO

from claude_vm import golden

# What an expression may name besides the entry point's options and what it makes itself: Ansible's own, Jinja's.
MAGIC = {"omit", "ansible_check_mode", "ansible_facts", "ansible_managed", "item", "lookup", "query", "q"}
MAGIC |= {"true", "false"}
# The facts a base template's identity covers, and those a task of the entry point gathers itself.
FACTS = {
    "architecture",
    "distribution",
    "distribution_release",
    "distribution_major_version",
    "os_family",
    "service_mgr",
    "virtualization_type",
    "getent_passwd",
    "getent_group",
}
# Names a VM is told apart by, or that reach other hosts.
FORBIDDEN = {"inventory_hostname", "inventory_hostname_short", "hostvars", "groups", "group_names", "vars"}
FORBIDDEN |= {"play_hosts"}
TESTED = ("when", "until", "failed_when", "changed_when")
ENV = jinja2.Environment(extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])


class _Any(dict):
    """Every filter and test name, as Ansible's are: what an expression reads is the question, not what it calls."""

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: object) -> object:
        return lambda *args, **kwargs: None

    def get(self, key: object, default: object = None) -> object:
        return self[key]


ENV.filters = _Any(ENV.filters)
ENV.tests = _Any(ENV.tests)


def _specs(role: str) -> dict:
    return yaml.safe_load((REPO / "roles" / role / "meta" / "argument_specs.yml").read_text())["argument_specs"]


def _yaml(path: Path) -> object:
    return (yaml.safe_load(path.read_text()) or {}) if path.is_file() else {}


def _expressions(node: object, key: str = "") -> list[str]:
    """Every Jinja expression in a task file's data: templated strings, and the bare ones of when and its like."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _expressions(v, str(k))
    elif isinstance(node, list):
        for v in node:
            out += _expressions(v, key)
    elif isinstance(node, str):
        if key in TESTED and "{{" not in node:
            out.append("{{ " + node + " }}")
        elif "{{" in node or "{%" in node:
            out.append(node)
    return out


def _made(paths: list[Path]) -> set[str]:
    """The names the reached task files make themselves: registers, facts they set, loop variables, task vars."""
    made: set[str] = set()
    for path in paths:
        if path.parent.name != "tasks":
            continue
        for task in golden.tasks(_yaml(path) or []):
            made.update([task["register"]] if "register" in task else [])
            made.update((task.get("ansible.builtin.set_fact") or {}).keys())
            made.update((task.get("vars") or {}).keys())
            made.update([(task.get("loop_control") or {}).get("loop_var", "item")])
    return made


def _calls(tree: jinja2.nodes.Node) -> list[tuple[str, list]]:
    return [
        (node.node.name, [arg.value if isinstance(arg, jinja2.nodes.Const) else None for arg in node.args])
        for node in tree.find_all(jinja2.nodes.Call)
        if isinstance(node.node, jinja2.nodes.Name)
    ]


def _fact_keys(tree: jinja2.nodes.Node) -> set[str]:
    keys = set()
    for node in tree.find_all((jinja2.nodes.Getitem, jinja2.nodes.Getattr)):
        if isinstance(node.node, jinja2.nodes.Name) and node.node.name == "ansible_facts":
            key = node.arg.value if isinstance(node, jinja2.nodes.Getitem) else node.attr
            keys.add(key if isinstance(key, str) else "?")
    return keys


def _problems(text: str, allowed: set[str], files: Path, where: str) -> list[str]:
    """What an expression reads that the entry point may not."""
    tree = ENV.parse(text)
    problems = []
    for name in sorted(jinja2.meta.find_undeclared_variables(tree)):
        if name in FORBIDDEN or name.startswith("vault_"):
            problems.append(f"{where}: reads {name}")
        elif name not in allowed and name not in MAGIC:
            problems.append(f"{where}: reads {name}, which is no option of the entry point")
    for key in sorted(_fact_keys(tree) - FACTS):
        problems.append(f"{where}: reads the fact {key}")
    for name, args in _calls(tree):
        if name in ("lookup", "query", "q"):
            plugin = args[0] if args else None
            target = args[1] if len(args) > 1 else None
            # A file of the role's own, by a plain relative name: joined to files/, an absolute path is itself.
            own = isinstance(target, str) and (files / target).resolve().parent == files.resolve()
            if plugin != "ansible.builtin.file" or not own or not (files / target).is_file():
                problems.append(f"{where}: {name}({plugin!r}, {target!r}) is no file of the role's own files/")
    return problems


@cache
def _guard(role: str) -> list[str]:
    """Every problem the static guard finds in the role's image entry point."""
    specs = _specs(role)
    options = set(specs["image"]["options"])
    paths = golden.reached(role)
    root = REPO / "roles" / role
    internals = dict(_yaml(root / "vars" / "main.yml") or {})
    defaults = dict(_yaml(root / "defaults" / "main.yml") or {})
    made = _made(paths)
    problems = [
        f"{role}: the image option {name} is no_log"
        for name, spec in specs["image"]["options"].items()
        if spec.get("no_log")
    ]
    for name in sorted(options):
        if "vault_" in json.dumps(defaults.get(name, "")):
            problems.append(f"{role}: the default of the image option {name} names a vault variable")

    # A role's internal or another default is allowed when what it reads is, in turn.
    def allowed_internal(name: str, seen: frozenset = frozenset()) -> bool:
        value = internals.get(name, defaults.get(name))
        if name in seen or (name not in internals and name not in defaults):
            return False
        for text in _expressions(value):
            tree = ENV.parse(text)
            for each in jinja2.meta.find_undeclared_variables(tree):
                if each in options or each in MAGIC or each in made:
                    continue
                if each in FORBIDDEN or each.startswith("vault_") or not allowed_internal(each, seen | {name}):
                    return False
            if _fact_keys(tree) - FACTS:
                return False
        return True

    allowed = options | made | {name for name in {*internals, *defaults} if allowed_internal(name)}
    for path in paths:
        if path.suffix == ".j2":
            texts = [path.read_text()]
        elif path.parent.name == "tasks":
            texts = _expressions(_yaml(path))
        else:
            continue
        for text in texts:
            problems += _problems(text, allowed, root / "files", str(path.relative_to(REPO)))
    return problems


@pytest.mark.parametrize("role", golden.BAKED)
def test_an_image_entry_point_reads_only_its_options_and_the_os_facts(role):
    assert _guard(role) == []


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("{{ inventory_hostname }}", "reads inventory_hostname"),
        ("{{ hostvars['pve'].ansible_host }}", "reads hostvars"),
        ("{{ vault_vm_password }}", "reads vault_vm_password"),
        ("{{ common_hostname }}", "reads common_hostname, which is no option"),
        ("{{ ansible_facts['default_ipv4'] }}", "reads the fact default_ipv4"),
        ("{{ lookup('ansible.builtin.env', 'HOME') }}", "is no file of the role's own files/"),
        ("{{ lookup('ansible.builtin.file', '/etc/passwd') }}", "is no file of the role's own files/"),
        ("{{ lookup('ansible.builtin.pipe', 'id') }}", "is no file of the role's own files/"),
    ],
)
def test_the_guard_finds_each_kind_of_read_an_image_may_not_make(text, said):
    problems = _problems(text, {"common_user"}, REPO / "roles" / "dev_tools" / "files", "x.yml")
    assert len(problems) == 1 and said in problems[0], problems


@pytest.mark.parametrize(
    "text",
    [
        "{{ common_user }}",
        "{{ ansible_facts['architecture'] }}",
        "{{ lookup('ansible.builtin.file', 'docker.asc') }}",
        "{% for x in common_user %}{{ x | upper }}{% endfor %}",
        "{{ omit if ansible_check_mode else item }}",
    ],
)
def test_the_guard_lets_an_option_an_os_fact_and_a_file_of_the_role_s_own_through(text):
    assert _problems(text, {"common_user"}, REPO / "roles" / "dev_tools" / "files", "x.yml") == []


@pytest.mark.parametrize("role", golden.BAKED)
def test_image_options_records_exactly_the_image_entry_points_options_but_those_reading_facts(role):
    specs = _specs(role)
    assert specs["image_options"]["options"] == specs["image"]["options"]
    (task,) = _yaml(REPO / "roles" / role / "tasks" / "image_options.yml")
    recorded = task["ansible.builtin.set_fact"][f"{role}_image_options"]
    assert recorded == {name: "{{ " + name + " }}" for name in recorded}
    defaults = _yaml(REPO / "roles" / role / "defaults" / "main.yml")
    facts = {name for name in specs["image"]["options"] if "ansible_facts" in str(defaults.get(name, ""))}
    assert set(recorded) == set(specs["image"]["options"]) - facts


def test_the_vpn_and_editor_roles_are_never_baked():
    # The golden is independent of them: neither has an image entry point, and the digest reads none of their files.
    assert set(golden.BAKED) == {"common", "dev_tools", "claude_code"}
    for role in ("tailscale", "vscode_server", "github_projects"):
        assert not (REPO / "roles" / role / "tasks" / "image.yml").exists()
        assert "image" not in _specs(role)
    assert not [path for path in golden.files() if path.parts[-3] in ("tailscale", "vscode_server")]


def test_common_s_image_leaves_out_what_a_vm_is_told_apart_by_and_the_masked_units():
    names = {path.name for path in golden.reached("common") if path.parent.name == "tasks"}
    assert {"apt.yml", "user.yml", "sshd.yml", "profile.yml", "tmux.yml", "services.yml"} <= names
    assert not names & {"sudo.yml", "ssh_path.yml", "masked_units.yml", "main.yml"}


def test_claude_code_s_image_installs_and_does_nothing_else():
    names = {path.name for path in golden.reached("claude_code") if path.parent.name == "tasks"}
    assert names == {"image.yml", "install.yml", "install_native.yml", "install_npm.yml"}


# --------------------------------------------------------------------------------------- the digest and key ---

OPTIONS = {role: {"a": 1} for role in golden.BAKED}


def _copy(tmp_path: Path) -> Path:
    """A copy of what the digest reads, to change one file of."""
    for path in golden.files():
        target = tmp_path / path.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
    return tmp_path


def test_the_digest_is_the_same_for_the_same_files_and_options():
    assert golden.toolchain_digest(OPTIONS) == golden.toolchain_digest(json.loads(json.dumps(OPTIONS)))
    assert re.fullmatch(r"[0-9a-f]{64}", golden.toolchain_digest(OPTIONS))


def test_the_digest_changes_with_an_option():
    changed = {**OPTIONS, "dev_tools": {"a": 2}}
    assert golden.toolchain_digest(changed) != golden.toolchain_digest(OPTIONS)


def test_the_digest_changes_with_a_file_the_image_reaches_and_not_with_one_it_does_not(tmp_path):
    repo = _copy(tmp_path)
    before = golden.toolchain_digest(OPTIONS, repo=repo)
    (repo / "roles" / "claude_code" / "tasks" / "login.yml").write_text("---\n[]\n")
    assert golden.toolchain_digest(OPTIONS, repo=repo) == before
    (repo / "roles" / "claude_code" / "tasks" / "install.yml").write_text("---\n[]\n")
    assert golden.toolchain_digest(OPTIONS, repo=repo) != before


def test_the_digest_needs_each_baked_role_s_options_and_no_others():
    with pytest.raises(ValueError, match="each, and no others"):
        golden.toolchain_digest({"common": {}})
    with pytest.raises(ValueError, match="each, and no others"):
        golden.toolchain_digest({**OPTIONS, "tailscale": {}})


def test_the_key_changes_with_the_build_the_base_and_the_digest():
    digest = golden.toolchain_digest(OPTIONS)
    key = golden.golden_key(1, "base-a", digest)
    assert len({key, golden.golden_key(2, "base-a", digest), golden.golden_key(1, "base-b", digest)}) == 3
    with pytest.raises(ValueError):
        golden.golden_key(1, "", digest)
    with pytest.raises(ValueError):
        golden.golden_key(1, "base-a", "not-a-digest")


def test_the_walk_refuses_an_include_it_cannot_follow(tmp_path):
    root = tmp_path / "roles" / "r" / "tasks"
    root.mkdir(parents=True)
    (root / "image.yml").write_text("- ansible.builtin.include_tasks: '{{ which }}.yml'\n")
    with pytest.raises(ValueError, match="templated file name"):
        golden.reached("r", repo=tmp_path)
