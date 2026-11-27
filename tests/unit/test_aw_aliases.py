"""The aliases: lib/claude_vm/engine/aliases.py, `aw deploy --only ssh-config` and destroy's removal of a VM's alias
(card G8; docs/architecture/engine.md §16.4).

The SSH files are always the test's own: the managed file, the person's config and the product's known-hosts file
under the test's home, and ssh -G asked of this computer's ssh through F30's stand-in (tests/unit/aw_machine.py), which
reads only the configuration it is given. Carried from tests/unit/test_ssh_config.py, which stays as it is (but for the
1.x file's move, which is not built): one block a VM and the Include once, at the top, the user's own file kept and its
mode; a second write that changes nothing; a new address that rewrites one block; a removal of one block alone; a Host
of the user's own refused, several on a line, a wildcard none; the Include off, said; a domain in place of the address,
and never through the hop; each alias's key under its identity in the product's file, a changed key refused whatever a
`Host *` says, the checking made stricter alone, and every option the block writes itself refused; an identity not one
of this product's refused, and a name or an address that is not one plain word; a bad option that fails validation and
leaves the file alone; a name ssh would take for another host refused when named and left out fleet-wide, a name with a
dot given gets its alias and an alias a dotted name has is left alone fleet-wide; the origin recorded, a block with no
record recorded by its next write, a block of a host the fleet no longer has taken over, and one of another host of the
fleet refused when named and left out fleet-wide; each Include spelling ssh reads, an Include below a Host block, and
`$HOME`, which ssh does not expand; through the Proxmox host, `ProxyJump` after `User`, only once the route's targets
returned for the machine and only for one that takes the hop, none through a host that cannot be the hop, a jump of the
person's own in its place, and the lines that give the jump the host's key. From tests/unit/test_host_keys.py: a
deleted VM's key kept, and `~/.ssh/known_hosts` never written. Against the fake (tests/molecule/fake_pve_api.py), on a
whole fleet folder: `aw deploy --only ssh-config` gives the same file as `playbooks/ssh_config.yml` for the same fleet;
the prune removes this host's blocks of VMs that are gone and those no alias may have, and never another host's; a VM
the fleet does not own is left out, or refused when named; and `aw destroy` of an aliased VM leaves no block for it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from aw_kit import run_aw
from aw_machine import ssh_stand_in
from fleet_fixture import whole_fleet
from harness import ENGINE_POOL, REPO, _pve, _pve_vm, engine_grant, fake_fleet
from pki import proxmox_pki
from test_vm_names import FROM_THE_HOST, PLAYBOOKS, _playbook

from claude_vm import proxmox
from claude_vm.engine import aliases as engine_aliases
from claude_vm.engine import fleet, routes
from claude_vm.engine.aliases import Alias, Files, Rules
from claude_vm.engine.record import Record
from claude_vm.fleet import paths
from claude_vm.pve import ownership
from claude_vm.pve import record as block
from claude_vm.runs import job as jobs
from claude_vm.runs import rundir

IDENTITY = "agent-warehouse-4f1c0000-0000-4000-8000-000000000001"
OTHER = "agent-warehouse-4f1c0000-0000-4000-8000-000000000002"
ALPHA = Alias("alpha", "192.0.2.51", "dev", IDENTITY, workspace="/home/dev/projects")
BETA = Alias("beta", "192.0.2.52", "dev", OTHER, workspace="/home/dev/projects")
OPTIONS = {"ServerAliveInterval": "30"}
HOP = "root@192.0.2.7"
VMIDS = {"alpha": 9010, "beta": 9011, "gamma": 9012}
# The two settings of destroy's whose work cards G13 and G14 build, off for a run of this card's.
OFF = ("--set", "destroy_save_sessions=false", "--set", "destroy_release=false")
# The route this computer takes to the fake's VMs: straight to their addresses, as today's tests of the file take it.
DIRECT = ("--set", "vm_ssh_route=direct")


# ---------------------------------------------------------------- the files and the write ----


@pytest.fixture
def job(home):
    """A job of this process's own with its run's directory, as the run that writes the aliases has; gone after."""
    made = jobs.create(
        paths.jobs_dir(paths.home(REPO)), "deploy", "deploy --only ssh-config", "pve", [], {"kind": "terminal"}, True
    )
    rundir.make(made)
    yield made
    rundir.remove(made)
    jobs.drop(made)


@pytest.fixture
def files(home) -> Files:
    """The SSH files under the test's own home."""
    return Files.of({"HOME": str(home)})


@pytest.fixture
def ssh(tmp_path) -> str:
    """The stand-in ssh, whose -G is this computer's own ssh given the test's configuration."""
    return str(ssh_stand_in(tmp_path, []) / "ssh")


def _rules(ssh: str, home: Path, **given: object) -> Rules:
    values: dict = {"origin": "pve", "reached_by": ("pve", "127.0.0.1", "localhost"), "options": dict(OPTIONS)}
    return Rules(ssh=ssh, environ={"HOME": str(home)}, **{**values, **given})


def _write(job, files: Files, aliases_given, rules: Rules) -> engine_aliases.Written:
    return asyncio.run(engine_aliases.write(job, files, list(aliases_given), rules))


def _blocks(text: str) -> list[str]:
    return re.findall(r"^# BEGIN agent-warehouse: (\S+)$", text, flags=re.M)


def _resolve(ssh: str, home: Path, config: Path | str, alias: str = "alpha") -> dict[str, str]:
    """What the real ssh makes of alias from config alone, as an editor's ssh would."""
    said = subprocess.run(
        [ssh, "-G", "-F", str(config), alias],
        env={"HOME": str(home), "PATH": os.defpath},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(line.split(" ", 1) for line in said.splitlines() if " " in line)


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def test_a_write_puts_one_block_a_vm_and_the_include_at_the_top_and_a_second_changes_nothing(
    job, files, ssh, home
) -> None:
    done = _write(job, files, [ALPHA, BETA], _rules(ssh, home))
    assert done.written == ("alpha", "beta") and done.include == "added"
    managed = files.config.read_text()
    assert _blocks(managed) == ["alpha", "beta"]
    head = "# BEGIN agent-warehouse: alpha\n# origin: pve\nHost alpha\n    HostName 192.0.2.51\n    User dev\n"
    assert head in managed
    # What ssh reads and the one comment: no state of this project's, agent forwarding off as written.
    assert "# vmid" not in managed and "# workspace" not in managed
    assert managed.count("    ForwardAgent no\n") == 2 and "IdentityFile" not in managed
    assert managed.endswith("# END agent-warehouse: beta\n")
    assert files.user.read_text().splitlines() == [f"Include {files.config}"]
    assert _mode(files.user) == _mode(files.config) == "0o600" and _mode(files.config.parent) == "0o700"
    resolved = _resolve(ssh, home, files.config)
    assert (resolved["hostname"], resolved["user"], resolved["serveraliveinterval"]) == ("192.0.2.51", "dev", "30")
    # The run's scratch file is gone with the write: nothing of it is left in the run's directory.
    assert not rundir.path_for(job, engine_aliases.SCRATCH).exists()
    # And a second write changes nothing: neither file is written again.
    before = (files.config.stat().st_mtime_ns, files.user.stat().st_mtime_ns)
    again = _write(job, files, [ALPHA, BETA], _rules(ssh, home))
    assert again.written == ("alpha", "beta") and again.include == "there"
    assert (files.config.stat().st_mtime_ns, files.user.stat().st_mtime_ns) == before
    assert files.config.read_text() == managed


def test_a_new_address_rewrites_its_block_alone_and_the_include_stays_one_line(job, files, ssh, home) -> None:
    _write(job, files, [ALPHA, BETA], _rules(ssh, home))
    beta = files.config.read_text().split("# BEGIN agent-warehouse: beta")[1]
    _write(job, files, [Alias("alpha", "192.0.2.99", "dev", IDENTITY)], _rules(ssh, home))
    managed = files.config.read_text()
    assert _blocks(managed) == ["alpha", "beta"] and "192.0.2.51" not in managed
    assert managed.split("# BEGIN agent-warehouse: beta")[1] == beta
    assert files.user.read_text().count("Include ") == 1


def test_a_removal_takes_out_its_block_alone_and_leaves_the_user_s_file(job, files, ssh, home) -> None:
    files.user.parent.mkdir(mode=0o700)
    files.user.write_text("Host github.com\n    User git\n")
    _write(job, files, [ALPHA, BETA], _rules(ssh, home))
    user = files.user.read_text()
    assert asyncio.run(engine_aliases.remove(files, ["alpha"])) == ("alpha",)
    assert _blocks(files.config.read_text()) == ["beta"]
    # The Include stays: removing it would mean editing the user's file, and ssh skips a file that is not there.
    assert files.user.read_text() == user
    assert asyncio.run(engine_aliases.remove(files, ["alpha"])) == ()


def test_a_removal_with_no_managed_file_is_nothing(files) -> None:
    assert asyncio.run(engine_aliases.remove(files, ["alpha"])) == ()
    assert not files.config.exists() and not files.config.parent.exists()


@pytest.mark.parametrize("hosts", ["Host alpha\n    User me\n", "Host github.com alpha\n    User git\n"])
def test_an_alias_the_user_s_own_config_names_is_refused_and_nothing_is_written(job, files, ssh, home, hosts) -> None:
    files.user.parent.mkdir(mode=0o700)
    files.user.write_text(hosts)
    with pytest.raises(engine_aliases.Refused, match="already has a Host entry named alpha"):
        _write(job, files, [ALPHA], _rules(ssh, home))
    assert not files.config.exists() and files.user.read_text() == hosts


def test_a_wildcard_host_line_is_no_collision(job, files, ssh, home) -> None:
    files.user.parent.mkdir(mode=0o700)
    files.user.write_text("Host *\n    ServerAliveInterval 10\n")
    assert _write(job, files, [ALPHA], _rules(ssh, home)).written == ("alpha",)
    assert files.user.read_text().startswith(f"Include {files.config}\nHost *\n")


def test_the_include_off_leaves_the_user_s_file_alone_and_the_line_is_said(job, files, ssh, home) -> None:
    done = _write(job, files, [ALPHA], _rules(ssh, home, include=False))
    assert done.include == "off" and not files.user.exists()
    assert _blocks(files.config.read_text()) == ["alpha"]
    events: list = []
    ctx = SimpleNamespace(emit=lambda kind, machine, text, **data: events.append((kind, text)))
    engine_aliases.said_written(ctx, done, _rules(ssh, home, include=False), files, [ALPHA])
    said = [text for kind, text in events if kind == "report"]
    assert f"Put this line at its top: Include {files.config}" in said[-1]
    assert said[0].startswith("alpha: ssh alpha   |   ") and said[0].endswith("code alpha   (opens /home/dev/projects)")


def test_a_domain_replaces_the_address(job, files, ssh, home) -> None:
    _write(job, files, [Alias("alpha", "", "dev", IDENTITY)], _rules(ssh, home, domain="lan", include=False))
    assert _resolve(ssh, home, files.config)["hostname"] == "alpha.lan"
    assert "192.0.2" not in files.config.read_text()


def test_each_alias_names_its_key_under_its_identity_in_the_product_s_file(job, files, ssh, home) -> None:
    # The key is looked up under the VM's identity, never the address the VM reported, in the product's file alone,
    # so that the editor's connection takes the key every run holds and a VM given an old address is refused.
    _write(job, files, [ALPHA, Alias("beta", "192.0.2.52", "dev")], _rules(ssh, home, include=False))
    alpha, beta = _resolve(ssh, home, files.config), _resolve(ssh, home, files.config, "beta")
    assert alpha["hostkeyalias"] == IDENTITY and "hostkeyalias" not in beta
    assert alpha["userknownhostsfile"] == beta["userknownhostsfile"] == str(files.known_hosts)
    assert (alpha["checkhostip"], alpha["updatehostkeys"], alpha["forwardagent"]) == ("no", "false", "no")
    assert alpha["stricthostkeychecking"] == "accept-new"
    # The write files no key: the product's file and the person's own are left as they were.
    assert not files.known_hosts.exists() and not (files.config.parent / "known_hosts").exists()


@pytest.mark.parametrize(
    "identity", ["github.com", "agent-warehouse-1 ProxyCommand=x", f"{IDENTITY}\nProxyCommand x", "agent-warehouse-"]
)
def test_an_identity_that_is_not_one_of_ours_is_refused(job, files, ssh, home, identity) -> None:
    with pytest.raises(engine_aliases.Refused, match="Not writing an alias for 'alpha'"):
        _write(job, files, [Alias("alpha", "192.0.2.51", "dev", identity)], _rules(ssh, home))
    assert not files.config.exists()


def test_identity_file_agent_forwarding_and_options_are_written_when_set(job, files, ssh, home) -> None:
    rules = _rules(
        ssh,
        home,
        include=False,
        identity_file="~/.ssh/id_ed25519_vms",
        forward_agent=True,
        options={"ServerAliveInterval": "15", "ConnectTimeout": "5"},
    )
    _write(job, files, [ALPHA], rules)
    managed = files.config.read_text()
    assert "    IdentityFile ~/.ssh/id_ed25519_vms\n" in managed and "    ForwardAgent yes\n" in managed
    resolved = _resolve(ssh, home, files.config)
    assert (resolved["forwardagent"], resolved["serveraliveinterval"], resolved["connecttimeout"]) == ("yes", "15", "5")


def test_a_changed_key_is_refused_whatever_a_host_star_of_the_user_s_says(job, files, ssh, home, tmp_path) -> None:
    # ssh takes the first value it reads, and the managed file is read before the user's own entries.
    _write(job, files, [ALPHA], _rules(ssh, home, include=False))
    assert "    StrictHostKeyChecking accept-new\n" in files.config.read_text()
    both = tmp_path / "both.conf"
    both.write_text(files.config.read_text() + "Host *\n    StrictHostKeyChecking no\n    ForwardAgent yes\n")
    said = _resolve(ssh, home, both)
    assert said["stricthostkeychecking"] == "accept-new" and said["forwardagent"] == "no"


def test_host_key_checking_may_be_made_stricter_through_the_options(job, files, ssh, home) -> None:
    _write(job, files, [ALPHA], _rules(ssh, home, include=False, options={"StrictHostKeyChecking": "yes"}))
    assert files.config.read_text().count("StrictHostKeyChecking") == 1
    assert _resolve(ssh, home, files.config)["stricthostkeychecking"] == "true"


@pytest.mark.parametrize(
    "options",
    [{"StrictHostKeyChecking": "no"}, {"ForwardAgent": "yes"}, {"userknownhostsfile": "/dev/null"}, {"User": "root"}],
)
def test_an_option_the_alias_writes_itself_is_refused_in_the_options(job, files, ssh, home, options) -> None:
    with pytest.raises(engine_aliases.Refused, match="which the alias block writes itself"):
        _write(job, files, [ALPHA], _rules(ssh, home, options=options))
    assert not files.config.exists()


def test_a_bad_option_fails_validation_and_leaves_the_file_alone(job, files, ssh, home) -> None:
    _write(job, files, [ALPHA], _rules(ssh, home))
    before = files.config.read_text()
    with pytest.raises(engine_aliases.Refused, match="The alias of beta failed to validate"):
        _write(job, files, [BETA], _rules(ssh, home, options={"NoSuchOption": "1"}))
    assert files.config.read_text() == before and _blocks(before) == ["alpha"]


@pytest.mark.parametrize(
    "given",
    [
        Alias("al pha", "192.0.2.51", "dev"),
        Alias("alpha", "192.0.2.51 -oProxyCommand=x", "dev"),
        Alias("alpha", "192.0.2.51\nHost *", "dev"),
        Alias("-alpha", "192.0.2.51", "dev"),
        Alias("alpha", "192.0.2.51", "dev root"),
        Alias("alpha", "192.0.2.51", "dev", jump="root@x -oProxyCommand=y"),
    ],
)
def test_a_name_or_address_that_is_not_one_word_is_refused_and_nothing_is_written(job, files, ssh, home, given) -> None:
    with pytest.raises(engine_aliases.Refused, match="is not one plain word"):
        _write(job, files, [ALPHA, given], _rules(ssh, home))
    assert not files.config.exists()


def test_a_name_given_that_ssh_would_take_for_another_host_is_refused_and_nothing_is_written(
    job, files, ssh, home
) -> None:
    for name in ("3232235777", "pve", "github.com"):
        given = Alias(name, "192.0.2.53", "dev")
        with pytest.raises(engine_aliases.Refused, match=f"Not writing an alias for {re.escape(name)}"):
            _write(job, files, [given], _rules(ssh, home, chosen=(name, "x") if name != "github.com" else ("x",)))
    assert not files.config.exists()


def test_a_name_with_a_dot_given_gets_its_alias(job, files, ssh, home) -> None:
    done = _write(job, files, [Alias("lab.vm", "192.0.2.53", "dev")], _rules(ssh, home, chosen=("lab.vm",)))
    assert done.written == ("lab.vm",) and _blocks(files.config.read_text()) == ["lab.vm"]


def test_fleet_wide_a_name_ssh_would_take_for_another_host_is_left_out_and_the_rest_written(
    job, files, ssh, home
) -> None:
    given = [ALPHA, Alias("github.com", "192.0.2.53", "dev"), Alias("pve", "192.0.2.54", "dev")]
    done = _write(job, files, given, _rules(ssh, home, include=False))
    assert done.written == ("alpha",) and [each["name"] for each in done.left_out] == ["github.com", "pve"]
    events: list = []
    ctx = SimpleNamespace(emit=lambda kind, machine, text, **data: events.append((kind, text, data)))
    engine_aliases.said_written(ctx, done, _rules(ssh, home, include=False), files, given)
    [said] = [text for kind, text, _ in events if kind == "report" and text.startswith("No alias for")]
    assert "deploy github.com --only ssh-config, if it is yours" in said and "Rename pve in Proxmox" in said
    [line] = [text for kind, text, data in events if kind == "next" and text]
    assert line.endswith("deploy github.com --only ssh-config")


def test_fleet_wide_an_alias_a_dotted_name_was_given_by_name_is_left_as_it_was(job, files, ssh, home) -> None:
    dotted = Alias("lab.vm", "192.0.2.53", "dev")
    _write(job, files, [dotted], _rules(ssh, home, chosen=("lab.vm",)))
    kept = files.config.read_text()
    _write(job, files, [Alias("lab.vm", "192.0.2.99", "dev"), ALPHA], _rules(ssh, home))
    assert kept in files.config.read_text() and _blocks(files.config.read_text()) == ["lab.vm", "alpha"]


def test_each_block_records_its_host_and_ssh_reads_past_it(job, files, ssh, home) -> None:
    _write(job, files, [ALPHA], _rules(ssh, home))
    assert proxmox.ssh_config_blocks(files.config.read_text()) == [{"name": "alpha", "origin": "pve"}]
    assert _resolve(ssh, home, files.config)["hostname"] == "192.0.2.51"


def _planted(files: Files, name: str, origin: str = "") -> str:
    record = f"# origin: {origin}\n" if origin else ""
    planted = f"# BEGIN agent-warehouse: {name}\n{record}Host {name}\n    HostName 192.0.2.99\n"
    planted += f"# END agent-warehouse: {name}\n"
    files.config.parent.mkdir(mode=0o700, exist_ok=True)
    files.config.write_text((files.config.read_text() if files.config.exists() else "") + planted)
    return planted


def test_a_block_with_no_record_is_recorded_by_its_next_write(job, files, ssh, home) -> None:
    _planted(files, "alpha")
    _write(job, files, [ALPHA], _rules(ssh, home))
    assert proxmox.ssh_config_blocks(files.config.read_text()) == [{"name": "alpha", "origin": "pve"}]


def test_a_block_of_a_host_the_fleet_no_longer_has_is_taken_over(job, files, ssh, home) -> None:
    _planted(files, "alpha", "gone-host")
    assert _write(job, files, [ALPHA], _rules(ssh, home, others=("nuc",))).written == ("alpha",)
    assert proxmox.ssh_config_blocks(files.config.read_text()) == [{"name": "alpha", "origin": "pve"}]


def test_a_name_given_whose_alias_is_another_host_s_is_refused_and_nothing_is_written(job, files, ssh, home) -> None:
    planted = _planted(files, "alpha", "nuc")
    with pytest.raises(engine_aliases.Refused, match="the alias of that name is the VM of nuc"):
        _write(job, files, [ALPHA], _rules(ssh, home, others=("nuc",), chosen=("alpha",)))
    assert files.config.read_text() == planted


def test_fleet_wide_a_vm_whose_alias_is_another_host_s_is_left_out_and_the_rest_written(job, files, ssh, home) -> None:
    planted = _planted(files, "alpha", "nuc")
    done = _write(job, files, [ALPHA, BETA], _rules(ssh, home, others=("nuc",)))
    assert done.written == ("beta",) and done.theirs == ({"name": "alpha", "origin": "nuc"},)
    assert files.config.read_text().startswith(planted)


def test_a_removal_takes_out_a_block_whatever_it_records(files) -> None:
    _planted(files, "alpha", "nuc")
    _planted(files, "beta", "pve")
    assert asyncio.run(engine_aliases.remove(files, ["alpha", "beta"])) == ("alpha", "beta")
    assert files.config.read_text() == ""


# ---------------------------------------------------------------- the Include ----


def _ssh_reads_include(ssh: str, tmp_path: Path, line: str) -> bool:
    """Whether this computer's ssh reaches ~/.ssh/agent-warehouse.conf of a home through that Include spelling."""
    home = tmp_path / "probe-home"
    (home / ".ssh").mkdir(parents=True, exist_ok=True)
    (home / ".ssh" / "agent-warehouse.conf").write_text("Host alpha\n    HostName 192.0.2.99\n")
    config = home / ".ssh" / "config"
    config.write_text(f"{line}\n")
    return _resolve(ssh, home, config).get("hostname") == "192.0.2.99"


@pytest.mark.parametrize(
    "line",
    [
        "Include ~/.ssh/agent-warehouse.conf",
        "Include ${HOME}/.ssh/agent-warehouse.conf",
        "Include agent-warehouse.conf",
        "  include   ~/.ssh/agent-warehouse.conf  ",
    ],
)
def test_an_include_already_there_in_another_spelling_is_not_added_again(job, files, ssh, home, tmp_path, line):
    files.user.parent.mkdir(mode=0o700)
    before = f"{line}\n\nHost github.com\n    User git\n"
    files.user.write_text(before)
    _write(job, files, [ALPHA], _rules(ssh, home))
    # Not added again where this computer's ssh reads that spelling, added above it where it does not; the user's own
    # line is never rewritten.
    if _ssh_reads_include(ssh, tmp_path, line):
        assert files.user.read_text() == before
    else:
        assert files.user.read_text() == f"Include {files.config}\n{before}"
    assert _resolve(ssh, home, files.user)["hostname"] == "192.0.2.51"


def test_an_include_below_a_host_block_does_not_count(job, files, ssh, home) -> None:
    # ssh scopes an Include inside a Host block to that block: the line is added at the top, the user's own kept.
    files.user.parent.mkdir(mode=0o700)
    before = f"Host github.com\n    User git\n    Include {files.config}\n"
    files.user.write_text(before)
    _write(job, files, [ALPHA], _rules(ssh, home))
    assert files.user.read_text() == f"Include {files.config}\n{before}"
    assert _resolve(ssh, home, files.user)["hostname"] == "192.0.2.51"
    # And a second write leaves it at that.
    _write(job, files, [ALPHA], _rules(ssh, home))
    assert files.user.read_text() == f"Include {files.config}\n{before}"


def test_an_include_ssh_does_not_expand_is_not_taken_for_ours(job, files, ssh, home) -> None:
    files.user.parent.mkdir(mode=0o700)
    files.user.write_text("Include $HOME/.ssh/agent-warehouse.conf\n")
    _write(job, files, [ALPHA], _rules(ssh, home))
    assert files.user.read_text().splitlines() == [f"Include {files.config}", "Include $HOME/.ssh/agent-warehouse.conf"]


def test_the_mode_of_an_existing_user_config_is_left_alone(job, files, ssh, home) -> None:
    files.user.parent.mkdir(mode=0o700)
    files.user.write_text("Host github.com\n    User git\n")
    files.user.chmod(0o644)
    _write(job, files, [ALPHA], _rules(ssh, home))
    assert files.user.read_text() == f"Include {files.config}\nHost github.com\n    User git\n"
    assert _mode(files.user) == "0o644"


# ---------------------------------------------------------------- through the Proxmox host ----


class _Route:
    """A route through the Proxmox host as engine/routes.py's Route is read here: its targets give each machine a hop,
    none (a jump of its own), or raise; nothing is reached."""

    def __init__(self, through: bool = True, hop: bool = True, raises: Exception | None = None, key: str = "") -> None:
        self.through, self.hop, self.raises = through, hop, raises
        self.label = "pve (root@192.0.2.7)"
        self.proxy_jump = HOP
        self.settled = {"hop": {"user": "root", "host": "192.0.2.7", "port": None, "key": key}}
        self.asked: list[str] = []

    async def targets(self, machines):
        self.asked += [machine.name for machine in machines]
        if self.raises is not None:
            raise self.raises
        return tuple(SimpleNamespace(hop=object() if self.hop else None) for _ in machines)


def _machine(name: str = "alpha", address: str = "192.0.2.60") -> fleet.Target:
    return fleet.Target(ownership.KIND, name, IDENTITY, "pve", "pve", VMIDS.get(name, 9013), "dev", address)


def _jumps(route, rules: Rules, machines=None) -> tuple[dict, list[str]]:
    events: list = []
    ctx = SimpleNamespace(emit=lambda kind, machine, text, **data: events.append((kind, text)))
    found = asyncio.run(engine_aliases._jumps(ctx, route, machines or [_machine()], rules))
    return found, [text for kind, text in events if kind == "report"]


def test_through_the_proxmox_host_an_alias_jumps_only_once_its_targets_returned(ssh, home) -> None:
    route = _Route()
    assert _jumps(route, _rules(ssh, home)) == ({"alpha": HOP}, [])
    assert route.asked == ["alpha"]
    # A machine that brings a jump of its own (vm_ssh_options) keeps its own: no ProxyJump of the hop's.
    assert _jumps(_Route(hop=False), _rules(ssh, home))[0] == {"alpha": ""}
    # Direct, none, and nothing asked of the route.
    direct = _Route(through=False)
    assert _jumps(direct, _rules(ssh, home))[0] == {"alpha": ""} and direct.asked == []


def test_a_jump_of_the_user_s_own_in_the_options_takes_the_place_of_the_hop(ssh, home) -> None:
    route = _Route()
    assert _jumps(route, _rules(ssh, home, options={"ProxyJump": "me@bastion"}))[0] == {"alpha": ""}
    assert route.asked == []


def test_no_alias_goes_through_a_proxmox_host_that_cannot_be_the_hop(ssh, home) -> None:
    found, said = _jumps(_Route(raises=routes.Unusable("pve refused to forward to alpha")), _rules(ssh, home))
    assert found == {} and "No alias was written through pve (root@192.0.2.7)" in said[0]
    assert "pve refused to forward to alpha" in said[0] and "vm_ssh_route: direct" in said[0]
    # A login that failed as the route was settled: none either, and said.
    found, said = _jumps(None, _rules(ssh, home))
    assert found == {} and "which cannot be used as the hop" in said[0]


def test_a_machine_the_route_refuses_is_left_out_and_said_and_the_rest_go_on(ssh, home) -> None:
    class Choosy(_Route):
        async def targets(self, machines):
            if machines[0].name == "beta":
                raise routes.Refused("beta (VMID 9011) cannot be reached: its options")
            return await super().targets(machines)

    found, said = _jumps(Choosy(), _rules(ssh, home), [_machine("alpha"), _machine("beta", "192.0.2.61")])
    assert found == {"alpha": HOP} and said == ["No alias for beta: beta (VMID 9011) cannot be reached: its options"]


def test_through_the_proxmox_host_the_jump_follows_the_user_and_the_address_names_the_vm(job, files, ssh, home):
    host = SimpleNamespace(
        name="pve",
        hosts_named=("pve",),
        reached_by=("pve", "127.0.0.1"),
        host_flag="",
        environ={"HOME": str(home)},
        values={"claude_ssh_host_domain": SimpleNamespace(value="lan"), "ssh_executable": SimpleNamespace(value=ssh)},
    )
    # The host's sshd would resolve a VM's name itself: through the hop the domain is never used.
    rules = dataclasses.replace(Rules.of(host, (), through=True), include=False)
    assert rules.domain == "" and Rules.of(host, ()).domain == "lan"
    _write(job, files, [Alias("alpha", "192.0.2.60", "dev", IDENTITY, jump=HOP)], rules)
    lines = files.config.read_text().splitlines()
    assert lines[3:6] == ["    HostName 192.0.2.60", "    User dev", f"    ProxyJump {HOP}"]
    # ssh -G prints an address in brackets: the same jump.
    assert _resolve(ssh, home, files.config)["proxyjump"] in (HOP, "root@[192.0.2.7]")


@pytest.mark.parametrize(("named", "said"), [(False, True), (True, False)])
def test_the_lines_that_give_the_jump_the_host_s_key_are_said_where_ssh_would_not_offer_it(
    job, files, ssh, home, named, said
) -> None:
    key = str(home / ".ssh" / "id_pve")
    files.user.parent.mkdir(mode=0o700)
    files.user.write_text(f"Host 192.0.2.7\n    IdentityFile {key}\n" if named else "Host github.com\n    User git\n")
    events: list = []
    ctx = SimpleNamespace(job=job, emit=lambda kind, machine, text, **data: events.append((kind, text)))
    asyncio.run(engine_aliases._hop_key(ctx, _Route(key=key), files, _rules(ssh, home)))
    reports = [text for kind, text in events if kind == "report"]
    if said:
        [text] = reports
        assert f"Host 192.0.2.7\n      IdentityFile {key}" in text and str(files.user) in text
    else:
        assert reports == []


# ---------------------------------------------------------------- destroy's removal, without the fake ----


def _host(home: Path, ssh: str, others: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        name="pve",
        aliases=True,
        hosts_named=("pve", *others),
        reached_by=("pve", "127.0.0.1"),
        host_flag=" --host pve" if others else "",
        environ={"HOME": str(home)},
        values={"ssh_executable": SimpleNamespace(value=ssh)},
    )


def _forget(home: Path, ssh: str, others: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    events: list = []
    ctx = SimpleNamespace(emit=lambda kind, machine, text, **data: events.append((kind, text)))
    outcome = asyncio.run(engine_aliases.forget(ctx, _host(home, ssh, others), _machine(), "destroy"))
    return outcome, [text for kind, text in events if kind == "report"]


def test_destroy_removes_this_host_s_alias_of_the_vm_and_keeps_another_host_s(files, ssh, home) -> None:
    _planted(files, "alpha", "pve")
    _planted(files, "beta", "pve")
    assert _forget(home, ssh) == ("done", [])
    assert _blocks(files.config.read_text()) == ["beta"]
    theirs = _planted(files, "alpha", "nuc")
    outcome, said = _forget(home, ssh, ("nuc",))
    assert outcome == "done" and theirs in files.config.read_text()
    assert said == [
        "Kept the SSH alias of alpha (it records nuc): the alias of a name is the VM of the host its block records, and"
        " this run deleted pve's."
    ]


def test_destroy_with_no_alias_kept_writes_nothing(files, ssh, home) -> None:
    assert _forget(home, ssh) == ("done", [])
    assert not files.config.exists()


# ---------------------------------------------------------------- against the fake ----


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


def _uuid(base: str, vmid: int) -> str:
    return proxmox.smbios_uuid(_pve(base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"])


def _record(base: str, vmid: int, name: str) -> None:
    config = _pve(base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]
    made = Record(kind="vm", uuid=_uuid(base, vmid), stage="finished", name=name)
    notes = block.render(config.get("description", ""), made.fields())
    _pve(base, "PUT", f"nodes/pve/qemu/{vmid}/config", description=notes)


@pytest.fixture
def fake(home, tmp_path, pki):
    """A whole fleet against the fake: alpha and beta running and gamma stopped, each a member of the pool with a record
    of its own; and the stand-in ssh's folder, for each run's PATH."""
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        _pve(base, "PUT", "fake/agent-ready-after", polls=0)
        whole_fleet(home, base, directory, pki)
        for name, vmid in VMIDS.items():
            _pve(base, "PUT", "fake/pool-member", vmid=vmid, pool=ENGINE_POOL)
            _record(base, vmid, name)
        yield SimpleNamespace(base=base, home=home, ssh=ssh_stand_in(tmp_path, []), files=Files.of({"HOME": str(home)}))


def _deploy(fake, *words: str) -> subprocess.CompletedProcess[str]:
    return run_aw("deploy", *words, "--only", "ssh-config", *DIRECT, home=fake.home, first_on_path=(fake.ssh,))


def _said(ran: subprocess.CompletedProcess[str]) -> str:
    return " ".join((ran.stdout + ran.stderr).split())


def _ok(ran: subprocess.CompletedProcess[str]) -> None:
    assert ran.returncode == 0, f"exit {ran.returncode}\n--- stdout\n{ran.stdout}\n--- stderr\n{ran.stderr}"


def test_aw_deploy_only_ssh_config_gives_the_file_today_s_refresh_gives_for_the_same_fleet(
    fake, tmp_path, engine_guard
) -> None:
    # Today's playbooks/ssh_config.yml first, against the same fake, writing the same file; then the engine's.
    managed = fake.files.config
    settings = {
        **FROM_THE_HOST,
        "claude_ssh_config_file": str(managed),
        "claude_ssh_user_config": str(fake.files.user),
        "claude_ssh_config_include": False,
        "vm_ssh_route": "direct",
    }
    engine_guard.first_on_path(fake.ssh)
    report = _playbook(tmp_path, fake.base, PLAYBOOKS / "ssh_config.yml", settings=settings)
    assert report["rc"] == 0, report["stderr"][-2000:]
    today = managed.read_text()
    assert _blocks(today) == ["alpha", "beta"]
    managed.unlink()
    ran = _deploy(fake, "--set", "claude_ssh_config_include=false")
    _ok(ran)
    assert managed.read_text() == today
    said = _said(ran)
    assert "2 aliases refreshed in" in said
    assert "Not refreshed, no address (stopped, booting, or without the agent): gamma" in said
    assert f"Put this line at its top: Include {managed}" in said
    assert not fake.files.user.exists()


@pytest.mark.parametrize("sight", ["node", "pool"])
def test_a_fleet_wide_refresh_prunes_this_host_s_blocks_of_vms_gone_and_never_another_host_s(fake, sight) -> None:
    # The token sees a VM outside the pool only with VM.Audit on /vms (init's proxmox_audit_scope: node); under pool,
    # one it cannot see is, to the prune, one that is gone.
    if sight == "node":
        engine_grant(fake.base, "/vms", "VM.Audit")
    files = fake.files
    _planted(files, "gone", "pve")
    _planted(files, "unrecorded")
    theirs = _planted(files, "theirs", "nuc")
    _planted(files, "127.0.0.1", "pve")
    _pve_vm(fake.base, 9013, "delta", running=True, tags="agent-warehouse")
    _planted(files, "delta", "pve")
    ran = _deploy(fake, "--set", "claude_ssh_config_include=false")
    _ok(ran)
    text = files.config.read_text()
    said = _said(ran)
    assert theirs in text
    if sight == "node":
        assert _blocks(text) == ["theirs", "delta", "alpha", "beta"]
        assert "Pruned, no VM of that name exists any more: gone, unrecorded." in said
        assert "Left alone: delta. A VM of that name exists but is not the fleet's" in said
    else:
        assert _blocks(text) == ["theirs", "alpha", "beta"]
        assert "Pruned, no VM of that name exists any more: gone, unrecorded, delta." in said
    assert "Removed, a name no alias may have" in said and ": 127.0.0.1." in said
    assert "Left alone, recorded for a host this fleet does not have: theirs (nuc)." in said
    # Read-only against Proxmox, and no key filed or touched: neither the product's file nor the person's own.
    assert not files.known_hosts.exists() and not (files.config.parent / "known_hosts").exists()


def test_a_refresh_that_names_vms_prunes_nothing_and_a_vm_the_fleet_does_not_own_is_refused_named(fake) -> None:
    files = fake.files
    _planted(files, "gone", "pve")
    ran = _deploy(fake, "alpha", "--set", "claude_ssh_config_include=false")
    _ok(ran)
    assert _blocks(files.config.read_text()) == ["gone", "alpha"]
    assert "Only the VMs named were refreshed; a fleet-wide" in _said(ran)
    _pve_vm(fake.base, 9013, "delta", running=True, tags="agent-warehouse")
    _pve(fake.base, "PUT", "fake/pool-member", vmid=9013, pool=ENGINE_POOL)
    refused = _deploy(fake, "delta")
    assert refused.returncode != 0 and "delta" in refused.stderr, refused.stdout + refused.stderr
    assert _blocks(files.config.read_text()) == ["gone", "alpha"]
    # Fleet-wide it is left out and said, and the rest written.
    ran = _deploy(fake, "--set", "claude_ssh_config_include=false")
    _ok(ran)
    assert _blocks(files.config.read_text()) == ["alpha", "beta"] and "Left out: " in _said(ran)


def test_a_refresh_with_the_include_on_adds_it_once_and_says_how_to_connect(fake) -> None:
    files = fake.files
    files.user.parent.mkdir(mode=0o700, exist_ok=True)
    files.user.write_text("Host github.com\n    User git\n")
    ran = _deploy(fake)
    _ok(ran)
    assert files.user.read_text() == f"Include {files.config}\nHost github.com\n    User git\n"
    said = _said(ran)
    assert "alpha: ssh alpha | " in said and "code alpha" in said
    _ok(_deploy(fake))
    assert files.user.read_text().count("Include ") == 1


def test_with_the_managed_file_off_nothing_is_written(fake) -> None:
    ran = _deploy(fake, "--set", "claude_ssh_config=false")
    _ok(ran)
    assert "claude_ssh_config is false, so this project keeps no SSH aliases on this computer" in _said(ran)
    assert not fake.files.config.exists() and not fake.files.user.exists()


def test_destroy_of_an_aliased_vm_leaves_no_block_for_it_and_its_key_stays(fake) -> None:
    files = fake.files
    _ok(_deploy(fake, "--set", "claude_ssh_config_include=false"))
    assert _blocks(files.config.read_text()) == ["alpha", "beta"]
    line = f"agent-warehouse-{_uuid(fake.base, VMIDS['alpha'])} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHRlc3Q\n"
    files.known_hosts.write_text(line)
    ran = run_aw("destroy", "alpha", "--confirm=alpha", *OFF, home=fake.home, timeout=120)
    _ok(ran)
    assert _blocks(files.config.read_text()) == ["beta"]
    # The key stays under the deleted VM's identity, the tombstone that refuses any VM given it again.
    assert files.known_hosts.read_text() == line
    assert not (files.config.parent / "known_hosts").exists()


def test_a_write_that_met_the_file_changed_works_its_text_out_again_from_the_new_one(
    job, files, ssh, home, monkeypatch
) -> None:
    _write(job, files, [ALPHA], _rules(ssh, home))
    real = engine_aliases.swap
    raced: list[Path] = []

    def racing(path, before, after):
        # Another run's write lands between this one's read and its swap, once.
        if path == files.config and not raced:
            raced.append(path)
            _planted(files, "theirs", "nuc")
        return real(path, before, after)

    monkeypatch.setattr(engine_aliases, "swap", racing)
    _write(job, files, [BETA], _rules(ssh, home))
    assert raced and _blocks(files.config.read_text()) == ["alpha", "theirs", "beta"]
