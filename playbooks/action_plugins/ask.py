# Copyright (c) agent-warehouse contributors
# MIT License
"""pause's prompt, with the answers a question has carried to the terminal, for the menu the live engine asks it as
(lib/claude_vm/tui/choose.py; docs/architecture/custom-code.md §7.8).

Asks as ansible.builtin.pause does with a prompt and no timeout - the same text, the same answer in user_input, the
same Ctrl-C then C to go on or A to abort - and, where the question has a menu and is not hidden, hands prompt_until
a complete_input of Enter's two bytes and the menu marked (choose.marked): built-in bytes only, which cross to the
main process whether or not it can import the project's code. There the live engine asks a marked prompt as a menu
at a terminal that can draw; anywhere else, Ansible reads the typed answer as pause would. A hidden question is never
a menu: its answer is a secret, and a menu shows its answers.
"""

from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path

from ansible.errors import AnsibleError, AnsiblePromptInterrupt, AnsiblePromptNoninteractive
from ansible.module_utils.common.text.converters import to_text
from ansible.plugins.action import ActionBase
from ansible.utils.display import Display

_LIB = str(Path(__file__).resolve().parents[2] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

display = Display()


class ActionModule(ActionBase):
    """Ask a question at the terminal, as pause does, its answers carried for a menu where it has them."""

    BYPASS_HOST_LOOP = True

    def run(self, tmp=None, task_vars=None):
        from claude_vm.tui import choose

        result = super().run(tmp, task_vars or {})
        del tmp
        _, args = self.validate_argument_spec(
            argument_spec={
                "prompt": {"type": "str", "required": True},
                "echo": {"type": "bool", "default": True},
                "menu": {"type": "dict", "default": {}},
            }
        )
        echo = args["echo"]
        hidden = "" if echo else " (output is hidden)"
        prompt = f"[{self._task.get_name().strip()}]\n{args['prompt']}{hidden}:"
        menu = args["menu"] or {}
        complete = choose.marked(menu) if echo and menu.get("options") else None
        result.update(changed=False, rc=0, stderr="", stdout="", echo=echo)
        start = time.time()
        result["start"] = to_text(datetime.datetime.now())
        try:
            user_input = display.prompt_until(prompt, private=not echo, complete_input=complete)
        except AnsiblePromptInterrupt:
            user_input = None
        except AnsiblePromptNoninteractive:
            display.warning("Not waiting for response to prompt as stdin is not interactive")
            user_input = b""
        if user_input is None:
            try:
                user_input = display.prompt_until(
                    "Press 'C' to continue the play or 'A' to abort \r",
                    private=not echo,
                    interrupt_input=(b"a",),
                    complete_input=(b"c",),
                )
            except AnsiblePromptInterrupt:
                raise AnsibleError("user requested abort!") from None
        result["stop"] = to_text(datetime.datetime.now())
        result["delta"] = int(time.time() - start)
        result["stdout"] = f"Paused for {round((time.time() - start) / 60.0, 2)} minutes"
        result["user_input"] = to_text(user_input, errors="surrogate_or_strict")
        return result
