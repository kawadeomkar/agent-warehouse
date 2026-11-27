"""The system scenario's second driver: `deploy --remove-key`'s key taken off the container's account as the engine
takes one off, by guest/keys.py's `remove` (docs/architecture/testing.md §11.3).

side_effect.yml runs this here, with Molecule's Python, given the container's name and the key's fingerprint. It makes
a job and its run's directory in the scenario's fleet home, as converge.py does, and calls `keys.remove` whole - its
refusals, its read of the keys file, its one-task play through `layer1.run`, its read of the file again - with the two
roads to the machine that need an SSH server swapped for the container's: the keys file read with `docker exec` as the
account, and layer 1's child given its test-only connection, `community.docker.docker` as the account, never root. The
run's environment names no agent, and its HOME is a folder of the scenario's own, so that no key of this computer's is
read as one the run offers; the child keeps this computer's HOME, as converge.py's does. It prints one line of JSON,
the fingerprint and the lines taken, and exits 0 only where the key was taken off.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO / "lib"), str(HERE)]

import converge

from claude_vm.fleet import paths
from claude_vm.guest import keys, layer1
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir
from claude_vm.ssh import hostkeys, runner
from claude_vm.text import plain_text


def main(container: str, fingerprint: str) -> int:
    scenario = Path(os.environ["MOLECULE_EPHEMERAL_DIRECTORY"])
    environ = {"AGENT_WAREHOUSE_HOME": str(scenario / "aw"), "HOME": os.environ["HOME"]}
    home = paths.home(REPO, environ)
    identity = f"agent-warehouse-{uuid.uuid5(uuid.NAMESPACE_DNS, container)}"
    known_hosts = scenario / "known_hosts"
    made = runner.machine(identity, converge.ADDRESS, converge.ACCOUNT, scenario / "cp", known_hosts=known_hosts)
    target = dataclasses.replace(made, routed_as="direct", routed_from=runner.decided_from(made))
    hostkeys.file(known_hosts, identity, converge._key())
    real = layer1.run

    async def read(job, at, env, name) -> list[str]:
        done = subprocess.run(
            ["docker", "exec", "-u", converge.ACCOUNT, container, "sh", "-c", f"cd && cat {keys.AUTHORIZED}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if done.returncode != 0:
            raise keys.Failed(f"~/{keys.AUTHORIZED} on {name} could not be read: {done.stderr.strip()}")
        return plain_text(done.stdout).splitlines()

    async def run(job, at, play, variables_file=None, **given):
        given["env"] = {**given["env"], "HOME": os.environ["HOME"]}
        connection = {
            "ansible_connection": "community.docker.docker",
            "ansible_host": container,
            "ansible_user": converge.ACCOUNT,
        }
        return await real(
            job, at, play, variables_file, test_connection=connection, test_collections=converge._collections(), **given
        )

    keys._read = read
    layer1.run = run
    own_home = scenario / "remove-key-home"
    own_home.mkdir(exist_ok=True)
    env = {name: value for name, value in os.environ.items() if name != "SSH_AUTH_SOCK"}
    env["HOME"] = str(own_home)
    command = f"deploy {converge.NAME} --only configure"
    job = jobs.create(paths.jobs_dir(home), "deploy", command, None, [identity], {"kind": "terminal"}, True)
    try:
        rundir.make(job)
        removed = asyncio.run(
            keys.remove(
                job,
                target,
                fingerprint,
                name=converge.NAME,
                host_flag="",
                env=env,
                listed=converge.FLEET["vm_ssh_public_keys"],
            )
        )
    except (keys.Refused, keys.Failed) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        rundir.remove(job)
        jobs.drop(job)
    print(json.dumps({"fingerprint": removed.fingerprint, "lines": removed.lines}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: remove_key.py <the container's name> <the key's fingerprint>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
