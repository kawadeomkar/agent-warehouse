"""init's checks (lib/claude_vm/engine/init_checks.py; docs/architecture/engine.md §16.4).

`aw init --check`, and the end of `aw init`, run as a person runs them (aw_kit.run_aw), against the fake Proxmox
(harness.fake_fleet, with the engine's pool, role, user and token, its requests held to the engine's table) and a
whole fleet folder (fleet_fixture.whole_fleet), with a refusing ssh first on PATH as the local stand-in for the host's
root SSH. A host set up as init sets one up - the grants of E1's list, the engine's template, an SSH key - passes every
check, each said with today's `ok` prefix; the tests of tests/unit/test_init_check.py that hold today's checks, carried:
a privilege of A6's table the token lacks, named with its path; a template with no record of the engine's build, a
problem since deploy refuses it, and root SSH that fails where the template is missing, with the line that installs the
key; the template's bridge without SDN.Use; the hop's login where the route goes through the host. And the pool's mark
that names another fleet or a control VM, with D2's sentence; a pool that is not there, said, never a traceback;
`VM.Audit` on `/vms` missing (a `--` line) or with more beside it (a problem); the node settled and written where the
host's own settings.yml sets none, and `--check` writing nothing; a redirect, never followed. The exit status is 1
exactly when a `!!` line was said. The invariants with tests of their own: every call the checks make is a read (the
fake records each request's method, and anything but a GET fails), and no check runs while the certificate is not
settled (an impostor's refusal among them): init stops before any request that carries the token.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from aw_kit import run_aw
from fleet_fixture import Fleet, mark_pool, refusing_ssh, whole_fleet, with_port
from harness import (
    ENGINE_POOL,
    ENGINE_TOKEN,
    ENGINE_USER,
    REPO,
    _pve,
    _pve_requests,
    engine_grant,
    engine_token,
    fake_fleet,
)
from pki import proxmox_pki
from test_aw_engine_template import Fleet as Templates

from claude_vm import trust
from claude_vm.engine import init_checks
from claude_vm.fleet import overrides, paths, record
from claude_vm.pve import pin, privileges
from claude_vm.pve.client import Token
from claude_vm.secret import Secret

PROBLEM = init_checks.PREFIX[init_checks.PROBLEM]
OK = init_checks.PREFIX[init_checks.OK]
UNCHECKED = init_checks.PREFIX[init_checks.UNCHECKED]
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDoNotUse+/= dev@laptop"
OTHER = "00000000-0000-4000-8000-000000000000"
SETTINGS = "fleet/hosts/pve/settings.yml"
# The grants E1's list makes beside the role on the pool, each its own role here: the privileges the one table needs on
# the storage, the template's bridge and the node, and VM.Audit on every guest (proxmox_audit_scope: node).
GRANTS = {
    "/storage/local-lvm": ",".join(sorted(privileges.needed("storage"))),
    "/sdn/zones/localnetwork/vmbr0": ",".join(sorted(privileges.needed("bridge"))),
    "/nodes/pve": ",".join(sorted(privileges.needed("node"))),
    "/vms": "VM.Audit",
}


@dataclasses.dataclass(frozen=True)
class Lab:
    """A whole fleet folder against the fake (made), its state in directory, the certificates it serves, and the
    refusing ssh a run finds first on PATH."""

    made: Fleet
    directory: Path
    pki: dict
    ssh: Path

    def templates(self) -> Templates:
        return Templates(self.made.base, self.directory, Token(self.made.token, Secret(self.made.secret)), self.pki)

    def set_up(self, *, template: bool = True, **grants: str | None) -> None:
        """The host as init sets it up: GRANTS, each in grants (by its path's last part) given otherwise or left out;
        the engine's template where template says so; and the fleet's VMs an SSH key."""
        for path, privs in GRANTS.items():
            held = grants.get(path.rsplit("/", 1)[-1], privs)
            if held:
                engine_grant(self.made.base, path, held)
        if template:
            self.templates().built()
        where = paths.home(REPO)
        overrides.set(where, "vm_ssh_public_keys", [KEY], None, overrides.read(where, None).fleet.digest)
        # What whole_fleet's own init tried over root SSH (its checks, against no template) is not the test's.
        (self.ssh / "unrouted.log").unlink(missing_ok=True)

    def aw(self, *words: str):
        ran = run_aw("init", *words, home=self.made.home, first_on_path=(self.ssh,))
        assert ran.returncode == (1 if PROBLEM in ran.stdout else 0), ran
        return ran

    def sent(self) -> list[dict]:
        return [entry for entry in _pve_requests(self.directory) if entry["agent"] == pin.USER_AGENT]


def _files() -> dict[str, bytes]:
    """Every file of the fleet's home but its state folder, by its path there."""
    where = paths.home(REPO)
    return {
        str(path.relative_to(where.root)): path.read_bytes()
        for path in sorted(where.root.rglob("*"))
        if path.is_file() and paths.STATE not in path.relative_to(where.root).parts
    }


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def lab(home, tmp_path, pki):
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        made = whole_fleet(home, base, directory, pki)
        yield Lab(made, directory, pki, refusing_ssh(home))


def _lines(ran, prefix: str) -> list[str]:
    return [line for line in ran.stdout.splitlines() if line.startswith(prefix)]


# ---------------------------------------------------------------- a host set up passes every check ----


def test_every_check_passes_on_a_host_set_up_as_init_sets_one_up(lab) -> None:
    lab.set_up()
    files = _files()
    ran = lab.aw("--check")
    assert ran.returncode == 0, ran
    said = ran.stdout
    assert f"{OK}Proxmox VE 9.2.21 at 127.0.0.1:{lab.made.port} accepted {ENGINE_USER}!{ENGINE_TOKEN}" in said
    assert f"{OK}the pool {ENGINE_POOL} carries this fleet's mark (fleet {lab.made.fleet}, controller=none)" in said
    assert f"{OK}node pve: deploy creates the VMs there" in said
    assert f'{OK}The base template "ubuntu-24.04-cloudinit" (9000) is as its settings would build it.' in said
    assert (
        f"{OK}the token holds every privilege the engine uses on /pool/{ENGINE_POOL}, /storage/local-lvm,"
        " /sdn/zones/localnetwork/vmbr0, /nodes/pve" in said
    )
    assert f"{OK}storage local-lvm: the token can use it" in said and f"{OK}the VMs get 1 SSH key" in said
    assert not _lines(ran, PROBLEM) and not _lines(ran, UNCHECKED), said
    # The template is there: no root SSH at all, and --check wrote nothing.
    assert not (lab.ssh / "unrouted.log").exists()
    assert _files() == files
    # The invariant: every call the checks make is a read.
    assert {entry["method"] for entry in lab.sent()} == {"GET"}


def test_init_ends_with_the_same_checks_and_writes_the_node_it_settles_where_none_is_set(lab) -> None:
    lab.set_up()
    where = paths.home(REPO)
    overrides.unset(where, "proxmox_node", "pve", overrides.read(where, "pve").host.digest)
    files = _files()
    # init --check says the node, and writes nothing.
    checked = lab.aw("--check")
    assert checked.returncode == 1
    assert _lines(checked, PROBLEM) == [
        f"{PROBLEM}proxmox_node is not set for pve: it is pve, since it is the only node online (pve, pve2), and aw"
        " init writes it"
    ], checked.stdout
    assert _files() == files
    # init with nobody there writes it, once, and its checks pass.
    ran = lab.aw("--no-prompt")
    assert ran.returncode == 0, ran
    assert f"{OK}node pve: written into {SETTINGS} as proxmox_node, since it is the only node online (pve, pve2)" in (
        ran.stdout
    )
    assert overrides.read(where, "pve").host.settings["proxmox_node"] == "pve"
    assert {entry["method"] for entry in lab.sent()} == {"GET"}
    again = lab.aw("--no-prompt")
    assert again.returncode == 0 and "written into" not in again.stdout, again


# ---------------------------------------------------------------- the privileges ----


def test_a_privilege_of_the_table_the_token_lacks_is_named_with_its_path(lab) -> None:
    lab.set_up(**{"local-lvm": "Datastore.Audit"})
    ran = lab.aw("--check")
    assert ran.returncode == 1
    [line] = [each for each in _lines(ran, PROBLEM) if "lacks" in each]
    assert line.startswith(f"{PROBLEM}the token lacks Datastore.AllocateSpace on /storage/local-lvm: add it to"), line
    assert "pveum role modify AgentWarehouse --append 1 --privs 'Datastore.AllocateSpace'" in line
    assert "aw init at a terminal, with root SSH to the host, offers to (answer y)" in line


def test_a_path_the_token_holds_nothing_of_the_engine_s_on_says_the_grant(lab) -> None:
    lab.set_up(pve=None)
    ran = lab.aw("--check")
    assert ran.returncode == 1
    [line] = [each for each in _lines(ran, PROBLEM) if "/nodes/pve" in each]
    assert f"pveum acl modify /nodes/pve --roles AgentWarehouse --users {ENGINE_USER}" in line, line


def test_the_template_s_bridge_without_sdn_use_is_a_problem(lab) -> None:
    lab.set_up(vmbr0="SDN.Audit")
    ran = lab.aw("--check")
    assert ran.returncode == 1
    lacking = f"{PROBLEM}the token lacks SDN.Use on /sdn/zones/localnetwork/vmbr0"
    assert any(each.startswith(lacking) for each in _lines(ran, PROBLEM)), ran.stdout


def test_vm_audit_on_every_guest_missing_is_said_and_more_there_is_a_problem(lab) -> None:
    lab.set_up(vms=None)
    ran = lab.aw("--check")
    assert ran.returncode == 0, ran
    [line] = _lines(ran, UNCHECKED)
    assert line.startswith(f"{UNCHECKED}the token may not read /vms (VM.Audit), so status will not show"), line
    engine_grant(lab.made.base, "/vms", "VM.Audit,VM.PowerMgmt")
    ran = lab.aw("--check")
    assert ran.returncode == 1
    beyond = "VM.PowerMgmt on /vms, beyond VM.Audit, so it can change every VM"
    assert any(beyond in each for each in _lines(ran, PROBLEM)), ran.stdout


# ---------------------------------------------------------------- the pool and its mark ----


@pytest.mark.parametrize("marked", ["another fleet", "a control VM"])
def test_a_mark_that_names_another_fleet_or_a_controller_is_a_problem_with_d2_s_sentence(lab, marked) -> None:
    lab.set_up()
    if marked == "another fleet":
        mark_pool(lab.made.base, OTHER)
        said = f"the pool's mark names another fleet, {OTHER}, where this computer's folder is the fleet"
    else:
        mark_pool(lab.made.base, lab.made.fleet, controller=OTHER)
        said = f"this fleet has a control VM ({OTHER}): change it there, or retire it"
    ran = lab.aw("--check")
    assert ran.returncode == 1
    assert [each for each in _lines(ran, PROBLEM) if said in each], ran.stdout


def test_a_pool_that_is_not_there_is_a_problem_said_never_a_traceback(lab) -> None:
    lab.set_up()
    where = paths.home(REPO)
    overrides.set(where, "proxmox_pool", "not-there", "pve", overrides.read(where, "pve").host.digest)
    # Granted there, so that the read reaches the pool's absence, Proxmox's 500 (X6), past the privilege's 403.
    engine_grant(lab.made.base, "/pool/not-there", "Pool.Audit")
    ran = lab.aw("--check")
    assert ran.returncode == 1 and "Traceback" not in ran.stderr, ran
    assert any(each.startswith(f"{PROBLEM}the pool not-there could not be read") for each in _lines(ran, PROBLEM)), (
        ran.stdout
    )


# ---------------------------------------------------------------- the template and root SSH ----


def test_a_template_with_no_record_is_a_problem_since_deploy_refuses_it(lab) -> None:
    lab.set_up(template=False)
    lab.templates().member(9000)
    ran = lab.aw("--check")
    assert ran.returncode == 1
    refused = "keeps no record of this engine's build, so it is never rebuilt"
    assert any(refused in each for each in _lines(ran, PROBLEM)), ran.stdout
    # Refused, never rebuilt: root SSH is not asked.
    assert "root SSH" not in ran.stdout, ran.stdout
    assert not (lab.ssh / "unrouted.log").exists()


def test_root_ssh_that_fails_where_the_template_is_missing_says_how_to_install_the_key(lab) -> None:
    lab.set_up(template=False)
    where = paths.home(REPO)
    overrides.set(where, "proxmox_template_vmid", 9999, "pve", overrides.read(where, "pve").host.digest)
    ran = lab.aw("--check")
    assert ran.returncode == 1
    built = [each for each in _lines(ran, OK) if "is not on the host" in each]
    assert built and built[0].endswith("So deploy builds it, over root SSH"), ran.stdout
    [line] = [each for each in _lines(ran, PROBLEM) if "root SSH" in each]
    assert line.startswith(f"{PROBLEM}root SSH to 127.0.0.1 failed") and "ssh-copy-id root@127.0.0.1" in line, line
    assert (lab.ssh / "unrouted.log").exists()


# ---------------------------------------------------------------- the route ----


def test_where_the_vms_are_reached_through_the_host_a_hop_that_cannot_be_used_is_a_problem(lab) -> None:
    lab.set_up()
    where = paths.home(REPO)
    overrides.set(where, "vm_ssh_route", "host", "pve", overrides.read(where, None).client.digest)
    ran = lab.aw("--check")
    assert ran.returncode == 1
    assert [each for each in _lines(ran, PROBLEM) if "vm_ssh_route" in each], ran.stdout


# ---------------------------------------------------------------- the token's requests ----


def test_a_redirect_is_an_answer_never_followed(lab) -> None:
    lab.set_up()
    _pve(lab.made.base, "PUT", "fake/redirect", location=f"{lab.made.base}/api2/json/fake/elsewhere")
    ran = lab.aw("--check")
    assert ran.returncode == 1
    [line] = [each for each in _lines(ran, PROBLEM) if "redirect" in each]
    assert line.startswith(f"{PROBLEM}nothing that reads as Proxmox answered at https://127.0.0.1:"), line
    assert not [entry for entry in _pve_requests(lab.directory) if "elsewhere" in entry["path"]]


def test_check_takes_no_value_init_writes(lab) -> None:
    files = _files()
    ran = run_aw("init", "--check", "--storage", "local", home=lab.made.home, first_on_path=(lab.ssh,))
    assert ran.returncode == 1 and ran.stdout == "", ran
    assert "error: aw init --check runs the checks alone and writes nothing: it takes no --storage" in ran.stderr
    assert _files() == files


def test_no_check_runs_while_the_certificate_is_not_settled(home, tmp_path, pki) -> None:
    # A fingerprint that is not what the host presents, with nobody to ask and root SSH not answering: init stops
    # before anything that carries the token, and no check is said.
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"]), enforce=True):
        mark_pool(base, record.new_id())
        with_port(int(base.rsplit(":", 1)[1]))
        secret = tmp_path / "token-secret"
        secret.write_text(engine_token(directory)[1] + "\n")
        secret.chmod(0o600)
        sent = len(_pve_requests(directory))
        wrong = ":".join(["AB"] * 32)
        assert wrong != trust.pem_fingerprint(pki["node"])
        ran = run_aw(
            "init",
            "--no-prompt",
            "--certificate",
            wrong,
            "--token-secret-file",
            str(secret),
            home=home,
            first_on_path=(refusing_ssh(home),),
        )
        assert ran.returncode == 1, ran
        assert not any(line.startswith(tuple(init_checks.PREFIX.values())) for line in ran.stdout.splitlines()), ran
        assert not any(entry["token"] for entry in _pve_requests(directory)[sent:])
