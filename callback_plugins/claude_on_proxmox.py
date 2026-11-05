"""The default stdout callback, with the moments a run waits for the person set apart, and just deploy's live output.

A debug task tagged attention_browser - the page to approve in the browser -
is drawn as a block titled ACTION REQUIRED IN YOUR BROWSER instead of an
"ok: [host] =>" and a YAML dump, and a retried task with that tag says once
that it is waiting instead of a FAILED - RETRYING line every few seconds.
After PLAY RECAP it prints NEXT STEPS: what the report tasks recorded with
set_stats as still left to do, per VM and matter, the last record of each
winning (docs/architecture/custom-code.md §7.5) - read from those tasks' own
results, never from the run's custom stats, to which any module's result can
add: a VM's root makes the modules run there say what it likes.

A module's warnings, deprecations and failure message are the VM's to choose
too, and Display prints them raw, so every hook prints them through
lib/claude_vm/text.py's plain_summary; a dry run's diffs, a file's text as the
VM holds it, through plain_text.

Everything else is the default callback's, options included: a subclass
without DOCUMENTATION has no options, every hook then fails on its first
get_option, and Ansible only warns while each task's result disappears. Only
ansible.cfg wires this in; the tests and Molecule run their own callbacks.

A recipe drawn live sets CLAUDE_ON_PROXMOX_LIVE to what it runs (deploy alpha, say), and with it, at verbosity
0 and with a run log, every event goes to lib/claude_vm/ansible/engine.py, which draws the run rather than
streaming it (docs/guides/runs.md); this module then keeps only its classic output, the blocks and NEXT STEPS, which
the engine prints through it too. Any -v, or ANSIBLE_STDOUT_CALLBACK=default, is the stream as before.
"""

from __future__ import annotations

DOCUMENTATION = """
    name: claude_on_proxmox
    type: stdout
    short_description: the default output, with banners for the waits and NEXT STEPS at the end
    description:
      - The default callback, drawing debug results tagged attention_browser as a block, saying once that a
        retried task with that tag is waiting, and printing what set_stats recorded as left to do after the
        play recap.
      - With CLAUDE_ON_PROXMOX_LIVE set, as just deploy sets it, and at verbosity 0, the run drawn live at a
        terminal, or a line per step otherwise, every task's output in the log alone.
    extends_documentation_fragment:
      - default_callback
      - result_format_callback
    requirements:
      - set as stdout_callback in ansible.cfg
"""

import sys
from pathlib import Path

from ansible.plugins.callback.default import CallbackModule as DefaultCallback
from ansible.plugins.loader import filter_loader
from ansible.utils.vars import merge_hash

_LIB = str(Path(__file__).resolve().parents[1] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)
from claude_vm import plugin, text  # noqa: E402

plugin.own(_LIB)


# The filter the prompts use, so a prompt's block and a message's look the same.
banner = text.banner
from claude_vm.ansible import engine as live  # noqa: E402

WAITING_TAG = live.WAITING_TAG
DEBUG_ACTIONS = live.DEBUG_ACTIONS
STATS_ACTIONS = live.STATS_ACTIONS
# What a diff holds that the VM wrote: the file's text before and after, the
# headers naming it, and a diff a module prepared itself.
DIFF_TEXT = ("before", "after", "before_header", "after_header", "prepared")
# A wait's block names the sign-in it belongs to, by the role the task is in.
TOPICS = {
    "claude_code": "Claude Code sign-in",
    "github_projects": "GitHub sign-in",
    "tailscale": "Tailscale sign-in",
}
from claude_vm.run.next_steps import COLOR, NEXT_STATS, next_steps  # noqa: E402


class CallbackModule(DefaultCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "claude_on_proxmox"
    NEXT_STATS = NEXT_STATS
    COLOR = COLOR

    def __init__(self):
        super().__init__()
        self._waiting = set()
        # The live run's engine, when the run is drawn: decided when the playbook starts, once the verbosity is known.
        self._live = None
        # NEXT STEPS's records, {stat: {vm: {matter: line}}}, as the set_stats tasks made them.
        self._next = {}

    # --- the classic output --------------------------------------------------------------------------------------
    # Every block is banner's own width - 80 columns, or the terminal's when
    # narrower - the same as the prompts', which are drawn before any callback.

    def _banner_or_default(self, result):
        msg = result.result.get("msg")
        if WAITING_TAG in result.task.tags and result.task.action in DEBUG_ACTIONS and isinstance(msg, str):
            if self._last_task_banner != result.task._uuid:
                self._print_task_banner(result.task)
            name = result.task.get_name()
            role = name.split(" : ", 1)[0] if " : " in name else ""
            self._display.display(
                banner(
                    msg,
                    "ACTION REQUIRED IN YOUR BROWSER",
                    host=result.host.get_name(),
                    topic=TOPICS.get(role, ""),
                ),
                color=COLOR,
            )
            return True
        return False

    def _retry_once(self, result):
        # One line per wait and host, not one per retry: the instruction above
        # it stays on the screen.
        key = (result.task._uuid, result.host.get_name())
        if key in self._waiting:
            return
        self._waiting.add(key)
        left = result.result.get("retries", 0) - result.result.get("attempts", 0)
        try:
            seconds = left * int(result.task.delay)
        except (TypeError, ValueError):
            seconds = 0
        said = (
            f"about {round(seconds / 60)} minutes"
            if seconds >= 120
            else f"about {seconds} seconds"
            if seconds > 0
            else f"{left} more checks"
        )
        self._display.display(
            f"Waiting for you in the browser for {result.host.get_name()} ({said} left; Ctrl-C ends the run) ...",
            color=COLOR,
        )

    # --- NEXT STEPS -----------------------------------------------------------------------------------------------
    def _keep_records(self, result):
        """A set_stats task's NEXT STEPS records, merged as Ansible merges the run's custom stats. Only that action's:
        ansible-core adds a module result's ansible_stats to the custom stats too, so a module on a VM could write any
        VM's line there, or blank one."""
        if result.task.action not in STATS_ACTIONS:
            return
        # A loop's records are its items', in their order (provision.yml's and destroy.yml's records loop).
        looped = result.result.get("results")
        for each in looped if isinstance(looped, list) else [result.result]:
            stats = each.get("ansible_stats") if isinstance(each, dict) else None
            if not isinstance(stats, dict) or stats.get("per_host") or not isinstance(stats.get("data"), dict):
                continue
            for stat in NEXT_STATS:
                records = stats["data"].get(stat)
                if isinstance(records, dict):
                    if stats.get("aggregate", True) and isinstance(self._next.get(stat), dict):
                        records = merge_hash(self._next[stat], records)
                    self._next[stat] = records

    def next_records(self):
        """The records kept so far, {stat: {vm: {matter: line}}}: what a part of a run in parts saves for the next."""
        return dict(self._next)

    def next_steps_of(self):
        """NEXT STEPS' lines, from the records kept: never the run's custom stats (_keep_records)."""
        return next_steps(self._next)

    # --- what the VM chose to say ----------------------------------------------------------------------------------
    def _handle_warnings(self, res):
        with text.plain_events(self):
            current = self._current_task_result
            if self._live and current is not None:
                # Kept in the run's model as they are said: what a person reads of the run, plain.
                said = [each.event.msg for each in (current.warnings or [])] if res.get("warnings") else []
                said += [each.event.msg for each in (current.deprecations or [])] if res.get("deprecations") else []
                self._live.warned(said)
            super()._handle_warnings(res)

    def _handle_exception(self, result, use_stderr=False):
        with text.plain_events(self):
            super()._handle_exception(result, use_stderr=use_stderr)

    def _get_diff(self, difflist):
        diffs = difflist if isinstance(difflist, list) else [difflist]
        said = super()._get_diff([self._plain_diff(diff) for diff in diffs])
        if self._live and said:
            self._live.diffed(said)
        return said

    @staticmethod
    def _plain_diff(diff):
        if not isinstance(diff, dict):
            return diff
        return {
            key: text.plain_text(value) if key in DIFF_TEXT and isinstance(value, str) else value
            for key, value in diff.items()
        }

    # --- the playbook, each hook handed to the live engine when the run is drawn ------------------------------------
    def v2_playbook_on_start(self, playbook):
        # One filter lookup here, before the first worker is forked: the loader keeps the filters it found, and every
        # worker inherits them, rather than executing filter_plugins/ again at its first lookup, task by task
        # (docs/architecture/custom-code.md §7.10).
        filter_loader.get("bool")
        self._live = live.from_environment(self, playbook)
        super().v2_playbook_on_start(playbook)

    def v2_playbook_on_play_start(self, play):
        if self._live:
            self._live.play_start(play)
        super().v2_playbook_on_play_start(play)

    def v2_playbook_on_task_start(self, task, is_conditional):
        if self._live:
            self._live.task_start(task)
        super().v2_playbook_on_task_start(task, is_conditional)

    def v2_playbook_on_handler_task_start(self, task):
        if self._live:
            self._live.task_start(task)
        super().v2_playbook_on_handler_task_start(task)

    def v2_playbook_on_include(self, included_file):
        if self._live:
            self._live.include(included_file)
        super().v2_playbook_on_include(included_file)

    def v2_runner_on_start(self, host, task):
        if self._live:
            self._live.runner_start(host, task)
        super().v2_runner_on_start(host, task)

    def v2_runner_on_ok(self, result):
        self._keep_records(result)
        if self._live:
            if self._live.ok(result):
                super().v2_runner_on_ok(result)
        elif not self._banner_or_default(result):
            super().v2_runner_on_ok(result)

    def v2_runner_item_on_ok(self, result):
        if self._live:
            self._live.item_ok(result)
        super().v2_runner_item_on_ok(result)

    def v2_runner_on_skipped(self, result):
        if self._live:
            self._live.skipped(result)
        super().v2_runner_on_skipped(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if self._live:
            self._live.failed(result, ignore_errors)
        super().v2_runner_on_failed(result, ignore_errors)

    def v2_runner_item_on_failed(self, result):
        if self._live:
            self._live.item_failed(result)
        super().v2_runner_item_on_failed(result)

    def v2_runner_on_unreachable(self, result):
        if self._live:
            self._live.unreachable(result)
        super().v2_runner_on_unreachable(result)

    def v2_runner_retry(self, result):
        if self._live:
            if self._live.retry(result):
                super().v2_runner_retry(result)
        elif WAITING_TAG not in result.task.tags:
            super().v2_runner_retry(result)
        else:
            self._retry_once(result)

    def v2_on_file_diff(self, result):
        # A dry run is read for its diffs: drawn, they go above the region, to the screen and the log.
        if self._live:
            with self._live.say():
                super().v2_on_file_diff(result)
            return
        super().v2_on_file_diff(result)

    def v2_playbook_on_stats(self, stats):
        if self._live:
            self._live.stats(stats, lambda s: DefaultCallback.v2_playbook_on_stats(self, s))
            return
        super().v2_playbook_on_stats(stats)
        lines = self.next_steps_of()
        if lines:
            self._display.display(banner(lines, "NEXT STEPS"), color=COLOR)
