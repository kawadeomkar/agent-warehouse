# Testing

What the suite runs, and how to run it. How it is built - the harness, each scenario, the fakes - is
[Architecture § 11](../architecture/testing.md); the workflow around a change is in
[CONTRIBUTING.md](https://github.com/kawadeomkar/agent-warehouse/blob/main/CONTRIBUTING.md).

```bash
just lint                     # every linter: the lock, yamllint, ansible-lint (production), ruff, just --fmt --check, --syntax-check, the other pre-commit hooks
just lint syntax              # one of them: lock, yaml, ansible, python, just, syntax or hooks
just test                     # just lint, then everything below but the last two: the local quality gate
just test unit                # pytest over tests/unit/, a worker per CPU (plain pytest: one test at a time), no network
just test unit --shard 2/6    # one of six shards of those, as CI splits them: the tests that share a run together
just test engine              # only the new engine's tests, tests/unit/test_aw_*.py, which unit runs too: seconds
just test --short             # lint, the engine's tests and today's that read the tree: for a change to no file of main's but CLAUDE.md, docs/ and zensical.toml
just test molecule            # every Molecule (Docker) scenario: the three below
just test molecule --jobs 4   # the same, four at a time: each in a log of its own (needs the disk and memory for four)
just test role                # each role's scenario, incl. idempotence; --role common,dev_tools for some of them
just test integration         # playbooks/configure.yml end to end in a container
just test provision           # provision.yml + discover.yml against a fake Proxmox API
MOLECULE_PROVISION_VERIFY=verify_code.yml just test provision   # one half of it, as one of CI's two jobs
just deploy --check           # dry run of configure.yml against your real VM (--check --diff)
just vagrant                  # configure.yml on a real VirtualBox VM (just vagrant destroy removes it)
```

What each unit test file holds is in [§11.1](../architecture/testing.md#111-unit--just-test-unit), and how
CI splits them into shards beside it.

No test reads or writes your own fleet folder. The engine being built for 3.0 keeps a fleet where
`lib/claude_vm/fleet/paths.py` says ([§16.9](../architecture/engine.md#169-fleet--the-fleets-files-the-settings-the-secrets-and-the-fleets-mark)):
`.aw/` in a checkout, or `~/.config/agent-warehouse/` with its state in `~/.local/state/agent-warehouse/` and its
key file in `~/.local/share/agent-warehouse/` for an installed program, and `AGENT_WAREHOUSE_HOME` moves all of it.
Every unit test runs with `AGENT_WAREHOUSE_HOME` in a directory of its own (the `aw_home` fixture in
`tests/unit/conftest.py`), every run the harness starts is given it, and a run of the suite fails, naming each path,
if a test made, changed or removed one of the names `paths.py` owns in a fleet folder of yours: the installed
program's three, this checkout's `.aw/`, the main worktree's `.aw/` when the suite runs from another worktree, and the
folder your own `AGENT_WAREHOUSE_HOME` names, if you exported one. In a checkout's `.aw/` it watches `client.yml`,
`fleet/` and the key, not `state/`, so your own `aw` runs there while a suite runs. It reads their names alone and
opens no file there.

The new engine's tests are the files `tests/unit/test_aw_*.py`, every test of which is marked `engine`, and
`just test engine` runs them alone, in seconds. For a change that touches no file of `main`'s but `CLAUDE.md`,
`docs/` and `zensical.toml`, and adds none to a folder today's product reads whole, `just test --short` lints and runs
them with the tests of today's suite that read the tree, in minutes, and refuses any other change, naming its files ([§11.1](../architecture/testing.md#111-unit--just-test-unit)). Write one with `tests/unit/aw_kit.py`: the `home` fixture for the
test's own home, and `run_aw` (or `aw_at_a_terminal`) to run `tools/aw`, never a `subprocess` call of your own, which
`tests/unit/test_aw_guards.py` refuses. A run inherits only the variables it names and fails its test if it calls a
browser opener, a clipboard, or this machine's `tailscale`, `gh`, `claude`, `ssh` or `ansible-playbook`; no test
touches your login keychain, whose programs, `security` and `secret-tool`, are failing stand-ins there too. A secret
the engine holds is a `Secret` (`lib/claude_vm/secret.py`), never shown, and `searched_for` looks for a canned one in
what a run left ([§11.1](../architecture/testing.md#111-unit--just-test-unit)).

The test's own process is guarded the same way, by changes of the guard's own that your test's `monkeypatch.undo()`
leaves in place: `HOME` is the test's own without asking, and a test never sets `PATH`. A stand-in of its own, an `ssh`
say, goes first with `engine_guard.first_on_path(dir)` in the test's own process, or `first_on_path=(dir, ...)` for a
run, a folder of the test's own directory either way; a test whose `PATH` finds this machine's own programs ahead of
the stand-ins fails. A fixture wider than one test is made before that guard holds, so `tests/unit/test_aw_guards.py`
refuses one its `WIDER_THAN_A_TEST` does not name with why. And an `aw` command a test file names, in a NEXT STEPS line
say, is held to the one table of verbs and options (`tests/unit/test_commands.py`): a made-up option fails the suite.

At a terminal, `just lint` is drawn live, a row for each check, the running one's last line beside it and the
ones to come listed; a check that fails prints its last lines above, and the summary names the checks it left
unrun. Every line the checks print goes to `.logs/<stamp>-lint.log`. `just test` is drawn the same way, as one run:
lint as its first stage, then the unit tests - a count once pytest has collected them, the tests running now, each
failure's words above the moment it fails - then each Molecule scenario a row, its actions beneath, a failed
action's task and Ansible's words above as Molecule goes on through its cleanup, and why it failed once Molecule
says so at the end; the summary lists what failed by name, and the stages a failure left unrun. Piped or in CI, or with a `-v` in `ANSIBLE_ARGS`, each part runs as before
([During a run](../guides/runs.md)).

Run Molecule through `just test`. Molecule picks `ansible-playbook` from `PATH` and only appends the project's
environment, so a system-wide Ansible would silently be used instead of the pinned one. By hand, put the
environment's `bin` first: `PATH="$(tools/env-run --bin):$PATH" tools/env-run molecule test`
([§13](../architecture/toolchain.md)).

Molecule scenarios use `geerlingguy/docker-ubuntu2404-ansible` (pinned by digest) with systemd, and share
`.config/molecule/config.yml`. Everything that would need a hypervisor or a third-party account is faked
so the tests are deterministic: what each scenario proves is in
[§11.2](../architecture/testing.md#112-role-scenarios--just-test-role) and [§11.3](../architecture/testing.md#113-playbook-scenarios--just-test-integration-and-provision), and every
fake and stand-in in [§11.4](../architecture/testing.md#114-the-fakes--testsmolecule). The scenarios that
install packages download them, so they need internet access
([§11.6](../architecture/testing.md#116-which-scenarios-need-the-network)). They also run what they
download as root - the real installers and apt repositories the roles use - inside containers that are
`privileged: true` with the host's cgroups, which systemd in them needs. A privileged container is no
boundary, so an upstream that served something malicious would reach the machine running Docker - on
macOS, Docker Desktop's VM, which mounts your home folder. Run `just test` in full on a throwaway VM rather
than on the machine that holds your vault and keys.

Not covered by Molecule: the npm install method for Claude Code, reinstalling on an exact-version bump,
and `common_user_passwordless_sudo: false`. `just vagrant` exercises `configure.yml` on a real VM
including the Docker service; qemu-guest-agent stays stopped there, since VirtualBox gives the VM no
port for it.

## Continuous integration

`.github/workflows/ci.yml` runs lint and the unit tests, every Molecule scenario, a dependency review
and, weekly, a canary against what the project installs from outside the repository. The new engine's
tests (`just test engine`) run there on Ubuntu and on a hosted macOS runner, each on Python 3.13 and
3.14, so a change is tried on macOS on a throwaway machine, never on your own. What each job does is
in [§12](../architecture/ci.md). No job needs a secret, so the full suite runs on pull requests
from forks; keep it that way.
