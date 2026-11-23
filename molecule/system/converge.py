"""The system scenario's driver: the system play converged on a container by layer 1's child, as the new engine
converges a machine (docs/architecture/testing.md §11.3).

converge.yml runs this here, with Molecule's Python, given the container's name. It makes a job and its run's directory
in a fleet home of the scenario's own (`MOLECULE_EPHEMERAL_DIRECTORY`), never the developer's; writes the play's
variables through the system play's own writer (guest/system.py, then guest/variables.py), which refuses what the engine
cannot serve yet; and starts the child through `layer1.run`, as the engine starts one, but with the child's test-only
connection, `community.docker.docker` to the container, and the folder of collections that connection is found in: the
one this scenario's own Ansible was given (ANSIBLE_COLLECTIONS_PATH), the first of it that holds community.docker. It
takes no address and starts no ssh. The machine's identity is made from the container's name, and a key is filed under
it in a known-hosts file of the scenario's own, since layer 1 refuses a machine with none; no ssh ever reads it.

Each event of the play is printed as it comes, a line each, and last one line of JSON: the child's exit status and its
counts, which `CONVERGED` in the scenario's own folder keeps too, a line for each converge. It exits 0 only where the
play ran whole and no task failed or was unreachable; converge.yml reads the line's `changed`, so that Molecule's
idempotence stage holds the second converge to none, and verify.yml reads the record, so that the first converge is
held to have changed something: an idempotence over a count that is always 0 would hold nothing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lib"))

from claude_vm.fleet import paths
from claude_vm.guest import layer1, system
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir
from claude_vm.ssh import hostkeys, runner

# The play's variables: what the guest play's scenario gives the same roles, as role variables. A container has no
# cloud-init to wait for; the person's key, a placeholder; a global npm package, so that every task of the roles that
# calls a Galaxy collection today runs here; no Docker Engine, whose daemon cannot run in the container.
DOCUMENT = {
    "configure_wait_for_cloud_init": False,
    "common_user_ssh_public_keys": [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMoleculeTestKeyDoNotUse0000000000000000000 molecule@test"
    ],
    "dev_tools_install_docker": False,
    "dev_tools_npm_global_packages": ["cowsay"],
}
# The machine's name, as a person would have chosen it, and an address of RFC 5737's documentation range: the test-only
# connection reaches the container by its name, never an address.
NAME = "system"
ADDRESS = "192.0.2.10"
ACCOUNT = "dev"
# The record of every converge of this scenario, in its own folder: one line of JSON each.
CONVERGED = "converged.jsonl"


def _key() -> hostkeys.Key:
    """A made-up ed25519 host key, filed under the machine's identity: layer 1 refuses a machine with none, and the
    test-only connection never reads it."""
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"\x07" * 32
    return hostkeys.Key("ssh-ed25519", base64.b64encode(blob).decode())


def _collections() -> Path:
    """The first folder of this scenario's own ANSIBLE_COLLECTIONS_PATH that holds community.docker."""
    for folder in os.environ.get("ANSIBLE_COLLECTIONS_PATH", "").split(os.pathsep):
        if folder and (Path(folder) / "ansible_collections" / "community" / "docker").is_dir():
            return Path(folder)
    raise SystemExit("no folder of ANSIBLE_COLLECTIONS_PATH holds community.docker: just test runs this scenario")


def _said(event: jobs.Event) -> None:
    data = event.data
    print(f"{data.get('status', event.kind)}: {data.get('part') or '-'}: {event.text}", flush=True)


def main(container: str) -> int:
    scenario = Path(os.environ["MOLECULE_EPHEMERAL_DIRECTORY"])
    home = paths.home(REPO, {"AGENT_WAREHOUSE_HOME": str(scenario / "aw"), "HOME": os.environ["HOME"]})
    identity = f"agent-warehouse-{uuid.uuid5(uuid.NAMESPACE_DNS, container)}"
    known_hosts = scenario / "known_hosts"
    target = runner.machine(identity, ADDRESS, ACCOUNT, scenario / "cp", known_hosts=known_hosts)
    hostkeys.file(known_hosts, identity, _key())
    command = f"deploy {NAME} --only configure"
    job = jobs.create(paths.jobs_dir(home), "deploy", command, None, [identity], {"kind": "terminal"}, True)
    try:
        rundir.make(job)
        job.listen(_said)
        written = system.variables(rundir.path_for(job, "vars.json"), DOCUMENT, secrets=())
        ended = asyncio.run(
            layer1.run(
                job,
                target,
                system.PLAY,
                written,
                name=NAME,
                host_flag="",
                env=os.environ,
                test_connection={
                    "ansible_connection": "community.docker.docker",
                    "ansible_host": container,
                    "ansible_user": "root",
                },
                test_collections=_collections(),
            )
        )
    finally:
        rundir.remove(job)
        jobs.drop(job)
    counts = ended.counts
    if ended.status != 0:
        print(ended.said, file=sys.stderr)
    said = json.dumps(
        {"status": ended.status, "failed": counts.failed, "unreachable": counts.unreachable, "changed": counts.changed}
    )
    with open(scenario / CONVERGED, "a") as record:
        record.write(said + "\n")
    print(said)
    return 0 if ended.status == 0 and not counts.failed and not counts.unreachable else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: converge.py <the container's name>")
    sys.exit(main(sys.argv[1]))
