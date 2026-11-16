# Copyright (c) agent-warehouse contributors
# MIT License
"""vm_route: how this machine reaches the VMs of the run's Proxmox host, and the hop's own ssh, run here under the hard
limits of lib/claude_vm/route.py (docs/architecture/custom-code.md §7.17). Never a command task: an open that loses
a race to another never returns, and a forward to an address that drops packets says nothing for minutes.

- ``do: settle`` works out the route, ``{route, setting, hop, policy, ...}``: the rule's answer for ``vm_ssh_route``,
  and the hop's settings, each the Proxmox host's own as Ansible resolves an ssh setting - its variables, then
  ansible.cfg, then the environment - and its account ``vm_ssh_jump`` where that names one.
- ``do: login`` makes sure of the hop's shared connection, and fails with what ssh said where it cannot.
- ``do: forward`` makes one forward of a connection of its own to a VM's address, at its port as its connection
  resolves it, and fails only where the host refuses to forward or ssh itself fails.
- ``do: held`` makes sure of the shared connection, and never fails: a hop that is gone shows as each VM not answering.
- ``do: check`` holds each VM of ``names`` to the route before anything connects to it, by what its own ssh shows
  (``ssh -G``) of its whole line - ssh_args, then its common args, then its extra args, as its connection resolves
  each - and fails naming each VM that would not go where the route says.
- ``do: stop`` tells the shared connection to take no new sessions (``-O stop``, never ``-O exit``) and makes sure of
  it again: for a VM that answered a forward of its own but not the run's connection.

A failure of any of them - a refused setting, a file that cannot be made, an address that is not one - is the result's,
in words, never an exception, which a task's ``failed_when`` could not see.

Runs on this machine, and connects to nothing of the play's.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase

_LIB = str(Path(__file__).resolve().parents[2] / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)  # the controller's package first (docs/architecture/custom-code.md §7.10)

# The Proxmox host's ssh settings the hop is made of, by the ssh connection plugin's own option names.
_OPTIONS = ("host", "remote_user", "port", "private_key_file", "ssh_executable", "control_path_dir")
# A VM's: where its connection goes, and the words of its command line that decide its proxy.
_LINE = ("host", "port", "ssh_executable", "ssh_args", "ssh_common_args", "ssh_extra_args")


def _ssh_options(host_vars, options=_OPTIONS) -> dict:
    """The ssh connection plugin's options for a host with ``host_vars``, as Ansible resolves them for a connection:
    the host's variables, then ansible.cfg, then the environment, then the plugin's default."""
    from ansible import constants as C
    from ansible.plugins.loader import connection_loader

    connection_loader.get("ssh", class_only=True)  # its options' definitions, loaded
    definitions = C.config.get_configuration_definitions("connection", "ssh")
    names = {"inventory_hostname"} | {
        entry["name"] for option in options for entry in definitions[option].get("vars") or []
    }
    variables = {name: host_vars[name] for name in names if name in host_vars}
    return {
        option: C.config.get_config_value(option, plugin_type="connection", plugin_name="ssh", variables=variables)
        for option in options
    }


def _split(value) -> list[str]:
    from ansible.plugins.connection.ssh import Connection

    return Connection._split_ssh_args(str(value or ""))  # noqa: SLF001 - the words ssh is given, split as Ansible does


class ActionModule(ActionBase):
    """The route to the VMs, and the hop's own ssh."""

    _requires_connection = False

    def run(self, tmp=None, task_vars=None):
        from claude_vm import route

        result = super().run(tmp, task_vars or {})
        del tmp
        _, args = self.validate_argument_spec(
            argument_spec={
                "do": {
                    "type": "str",
                    "required": True,
                    "choices": ["settle", "login", "forward", "held", "check", "stop"],
                },
                "setting": {"type": "str", "default": "auto"},
                "host": {"type": "str", "default": ""},
                "jump": {"type": "str", "default": ""},
                # Never a default: the two places that take a new host key name it
                # (tests/unit/test_controller_writes.py).
                "policy": {"type": "str", "default": ""},
                "route": {"type": "dict", "default": {}},
                "address": {"type": "str", "default": ""},
                "name": {"type": "str", "default": ""},
                "names": {"type": "list", "elements": "str", "default": []},
            }
        )
        result["changed"] = False
        # A failure is the result's, never an exception: a task's failed_when reads it, where a hop that cannot be used
        # is said and the run goes on (the aliases, just init's report).
        try:
            self._do(route, args, task_vars or {}, result)
        except (AnsibleActionFail, route.HopRefused, OSError, ValueError) as failed:
            said = failed.message if isinstance(failed, AnsibleActionFail) else str(failed)
            result.update(failed=True, msg=str(said), ok=False, usable=False)
        return result

    def _do(self, route, args, task_vars, result) -> None:
        if args["do"] == "settle":
            if args["policy"] not in ("yes", "accept-new"):
                raise AnsibleActionFail(f"no host key policy {args['policy']!r}")
            result["route"] = self._settle(route, args, task_vars)
            return
        settled = args["route"]
        if settled.get("route") != "host":
            return
        hop = settled["hop"]
        if args["do"] == "check":
            unheld = self._unheld(route, settled, args["names"], task_vars)
            result["unheld"] = unheld
            if unheld:
                result.update(failed=True, msg=route.unheld_said(settled, unheld))
            return
        if args["do"] in ("login", "held", "stop"):
            if args["do"] == "stop":
                result["stopped"] = route.hop_stop(hop, settled["policy"])
            held = route.hop_held(hop, settled["policy"])
            result.update(ok=held["ok"], opened=held["opened"], said=held["said"])
            if not held["ok"] and args["do"] == "login":
                result.update(failed=True, msg=route.login_said(settled, held["said"]))
            return
        # The VM's port as its connection resolves it: an account that may forward only to the VMs' own port answers
        # there and nowhere else.
        host_vars = task_vars["hostvars"]
        vm = host_vars[args["name"]] if args["name"] and args["name"] in host_vars else {}
        port = _ssh_options(vm, ("port",))["port"] or 22
        verdict = route.hop_forward(hop, args["address"], settled["policy"], port=port)
        result.update(verdict, said_plain=route.forward_said(settled, args["name"], args["address"], verdict))
        if not verdict["usable"]:
            result.update(failed=True, msg=result["said_plain"])

    def _unheld(self, route, settled, names, task_vars) -> list[dict]:
        """Of ``names``, each whose connection would not go where the route says, ``{name, why}``, by what its own ssh
        shows of its whole line. A VM with no address, or reached by another connection than ssh, is reached by
        nothing here (destroy adds one with a local connection, to delete it)."""
        unheld = []
        for name in names:
            host_vars = task_vars["hostvars"][name]
            kind = str(host_vars.get("ansible_connection") or "ssh").rsplit(".", 1)[-1]
            if not host_vars.get("ansible_host") or kind != "ssh":
                continue
            line = _ssh_options(host_vars, _LINE)
            address = str(line["host"] or "")
            given = [*_split(line["ssh_common_args"]), *_split(line["ssh_extra_args"])]
            words = [*_split(line["ssh_args"]), *given]
            takes_hop = bool(host_vars.get("claude_vm_ssh_via"))
            # The port and user change no proxy; the VM's address is where ssh would go.
            ssh = line["ssh_executable"] or "ssh"
            shown = route.ssh_shown(ssh, words, address)
            alone = None if takes_hop else route.ssh_shown(ssh, given, address)
            why = route.vm_route_refusal(settled, address, takes_hop, shown, alone)
            if why:
                detail = route.unheld_detail(settled, address, takes_hop, line)
                unheld.append({"name": str(name), "why": f"{why} ({detail})" if detail else why})
        return unheld

    def _settle(self, route, args, task_vars) -> dict:
        setting = args["setting"] or "auto"
        if setting not in route.ROUTES:
            raise AnsibleActionFail(f"vm_ssh_route is one of {', '.join(route.ROUTES)}, not {setting!r}")
        none = {"route": "direct", "setting": setting, "hop": {}, "policy": args["policy"], "addresses": []}
        # With no Proxmox host in the inventory, VMs are the inventory's own: nothing to go through.
        if setting == "direct" or not args["host"]:
            return none
        host_vars = task_vars["hostvars"][args["host"]]
        options = _ssh_options(host_vars)
        try:
            account = route.account(args["jump"])
            hop = route.hop_settings(
                ssh=options["ssh_executable"] or "ssh",
                user=account or options["remote_user"] or "",
                host=options["host"],
                port=options["port"],
                key=options["private_key_file"] or "",
                control_dir=options["control_path_dir"] or "~/.ansible/cp",
                name=args["host"],
            )
        except route.HopRefused as refused:
            # Under auto, a hop that could not be made matters only where the rule would go through it.
            if setting == "auto" and route.route_rule("auto", route.addresses_of(options["host"])) == "direct":
                return none
            raise AnsibleActionFail(f"{refused} (vm_ssh_route: {setting})") from None
        settled = route.route_settle(setting, hop)
        return {
            **none,
            "route": settled["route"],
            "hop": hop if settled["route"] == "host" else {},
            "addresses": settled["addresses"],
        }
