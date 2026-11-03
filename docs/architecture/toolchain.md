# 13. Toolchain

Everything is pinned in `pyproject.toml`, locked with every package it pulls in (version and hash) in
`uv.lock`, and lives in the project's environment, which `tools/env-run` decides: a virtualenv of yours when
you bring one (`PYTHON=`, or `python3` as the shell finds it in the checkout when that is a virtualenv), and
otherwise the checkout's own. `just install` runs `uv sync --locked`, which refuses a lock that no longer
matches `pyproject.toml`: into one of yours `--inexact`, with Python downloads off, so the versions the lock
pins go in and nothing else of the environment is removed; into the checkout's own, which the first run
makes, `--managed-python --python 3.13` (`managed_python`), on a Python uv downloads, exactly the lock's.
That one lives in the checkout's folder outside it,
`${XDG_DATA_HOME:-~/.local/share}/claude-on-proxmox/checkouts/<folder>-<cksum of its path>`
(`tools/env-run --data`), never a `.venv` in the checkout, so a worktree or a second clone has its own; and
the Galaxy collections live in the same folder for every checkout (`tools/env-run --collections`, exported
as `ANSIBLE_COLLECTIONS_PATH` by every recipe), so one checkout's `just install` never changes another's.
`just clean env` deletes that folder. `just install` runs `ansible-galaxy` with `--no-cache`: its response
cache writes an empty entry before each request to Galaxy, and the retry after one that failed (a 502, a
timeout) reads that entry and stops with `Missing expected 'results' in ansible-galaxy cache` (ansible-core
2.21), so one bad answer from Galaxy failed the install. It installs only when `tools/check collections` finds a
collection not at its pin: for collections that all are, ansible-galaxy says `Nothing to do` and advises a
`--force` of its own, which `just install` does not take, so the recipe says they are current instead. Dependabot's uv
ecosystem moves the pins and the lock. uv writes the path the environment was
synced by into the first line of every command it installs, and a sync that installs nothing rewrites
none, so before the sync `tools/stale_scripts.py` names each locked package with a command that starts a
Python outside the environment's `bin` - by a `.venv` link into it, as `make venv` left them - and the
sync reinstalls those, then checks that none is left. Every recipe runs the tools from that
environment's `bin`, never from `PATH`, and refuses a Python that is not a virtualenv (`tools/env-run
--bin`), whose scripts directory would be a system-wide one; the git hooks and the Vagrantfile find it the
same way, through `tools/env-run`. The git hook `just init` installs is `tools/git-hook`, never the one
`pre-commit install` writes, which names the Python that installed it in the hooks folder every worktree of
a clone shares: `tools/git-hook` names none, so each checkout commits through its own environment. The playbooks' own pins are `[project]`'s dependencies, the
linters' and tests' the `dev` group, and the wiki's the `docs` group; `just install` installs all three. The versions are the ones
`pyproject.toml` pins - this page names what each tool is for, so that a bump cannot leave it stale:

| Tool | For |
|---|---|
| ansible-core | the playbooks and roles |
| ansible-lint, yamllint | `just lint`, with ruff |
| ruff | Python lint and format |
| molecule, with molecule-plugins' Docker driver and the `docker` package | the role and playbook scenarios |
| pytest | `just test unit` |
| pre-commit | the hooks run on commit, and the file checks CI runs with them |
| proxmoxer, requests | the `community.proxmox` collection, on the controller |
| docsible | `just docs`: each role's reference page - its variables, internal values, tasks and a flowchart of each task file |
| Zensical | the wiki in `docs/` built as a site (`just docs serve`, `just docs build`) |
| Markdown, pymdown-extensions | the site's rendering and its heading anchors, kept the ones GitHub makes |
| markdown-it-py | the pages rendered as GitHub does, for the test that compares the two |
| uv | installing into your environment (`just install`) and the lock (`just install --lock`); one of the two tools on `PATH` by design, and pinned in the `dev` group for CI |

**just**, the other tool on `PATH`, runs every command (`justfile`; [Commands](../reference/commands.md)).
It is yours to install, as uv is, 1.59.0 or later, from its own releases - Homebrew, `cargo install just`,
or the release archive checked against its `SHA256SUMS`; not PyPI's `rust-just`, a third party's own build
of it. The justfile's `set minimum-version` refuses an older one. CI installs upstream's x86_64 Linux
archive, checked against the SHA256 in `.github/actions/setup/action.yml`, at the version written there
as `JUST_VERSION`, in the justfile's `minimum-version` and as its `just_pin`; `test_justfile.py` holds the
three to one. Dependabot moves neither file, so moving to a new `just` is by hand: the three versions, and
the archive's line of the new release's `SHA256SUMS` as `JUST_SHA256`. The justfile's format is checked
(`just lint just`) only by the pinned version, which `just lint` says when it skips it.

Collections: `community.proxmox`, `community.general`, `community.docker` and `ansible.posix`, pinned
to exact versions in `requirements.yml`, with `community.library_inventory_filtering_v1`, which
`community.docker` depends on with a range: unlisted, it would install whatever Galaxy published last.

> **Molecule resolves `ansible-playbook` from `PATH` and only appends the virtualenv**, so the
> environment's `bin` must be **prepended** or a different ansible-core is silently used. `just test`
> does this; a hand-rolled `molecule test` does not.

`ansible.cfg` sets the directory inventory, `roles_path`, the local `filter_plugins`, YAML callback
output, `profile_tasks`, `forks = 20`, pipelining, and `StrictHostKeyChecking=accept-new`. There is
deliberately no fact cache: the roles read only a few gathered facts, the one gather per configure run
costs seconds in a run that takes minutes, and a cached entry keyed by inventory name outlived the VM it
described, so a VM rebuilt under the same name within the hour was configured with the old VM's
`distribution_release` and `architecture`. Gathered facts are read only as `ansible_facts['<name>']`:
`inject_facts_as_vars = false`, set in Molecule's shared config too, stops Ansible copying each to a
top-level `ansible_architecture`, as ansible-core 2.24 will, so a read of one fails now rather than on
that upgrade; `tests/unit/test_gathered_facts.py` scans for one on a path no scenario takes. `forks`
is the number of VMs cloned, booted or configured at once - every `proxmox_vm` task is a
localhost-delegated API call, and `configure` is SSH to the fleet - so it is set above the default and
`ANSIBLE_ARGS="-f N"` raises it for a larger fleet. Pipelining sends each module over stdin, so a
module that transfers no file gets no per-task temporary directory, and the ones `copy` and `template`
make belong to the connecting user, made without become: the first module running as root that needs
one creates root's `remote_tmp` itself and warns that it did - `get_url` building the template on a new
Proxmox host, `deb822_repository` in `dev_tools` on a new VM. So `template.yml` and `configure.yml` make
it first (`playbooks/tasks/remote_tmp.yml`), as that warning asks, at the `remote_tmp` the run is
configured with. Setting `inventory_ignore_extensions` *replaces* Ansible's default ignore list rather
than extending it, so the value in `ansible.cfg` re-lists the defaults worth keeping and adds
`.example`; drop one and a stray `hosts.yml.bak` would be parsed as inventory.

Linting is the ansible-lint **production** profile with `args`, `empty-string-compare`,
`no-log-password`, `no-same-owner`, `name[prefix]` and `yaml` additionally enabled.
