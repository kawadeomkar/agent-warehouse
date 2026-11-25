"""aw status against one host of a fleet of two (lib/claude_vm/cli/status.py and status_inputs.py; docs/architecture/
engine.md §16.3): the new path's cases of tests/unit/test_status_hosts.py, which stays as it is and holds today's
status.

The fleet is E4's folder with a second host, nuc, set up by init against a fake Proxmox of its own, each with a token of
its own. A run reads the host --host names, else the current one (client.yml's current_host), and nothing of the other:
its API, its token and its settings are the host's own, every command a line names ends with ` --host <host>`, and the
other host's name is one no VM may be named after. `aw status --host nuc --json` sends its GETs to nuc's fake alone,
none to pve's. Reading every host at once, a child each, as today's status does, waits for card G16, which gives the
table the word that starts the child.
"""

from __future__ import annotations

import json
import os

import pytest
from aw_kit import run_launcher
from fleet_fixture import HOST, refusing_ssh, whole_fleet
from harness import ENGINE_TOKEN, ENGINE_USER, REPO, _pve_requests, fake_fleet
from pki import proxmox_pki
from test_aw_cli_imports import _checkout

from claude_vm.cli import status_inputs
from claude_vm.fleet import hosts, overrides, paths

OTHER = "nuc"


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def two(home, tmp_path, pki, engine_guard):
    """E4's fleet with its host pve, then nuc beside it against a fake of its own: pve's, nuc's, each fake's folder."""
    first_dir, second_dir = tmp_path / f"fake-{HOST}", tmp_path / f"fake-{OTHER}"
    first_dir.mkdir()
    second_dir.mkdir()
    for first_base in fake_fleet(first_dir, engine=True, certificate=(pki["cert"], pki["key"])):
        pve = whole_fleet(home, first_base, first_dir, pki)
        for second_base in fake_fleet(second_dir, engine=True, certificate=(pki["cert"], pki["key"])):
            nuc = whole_fleet(home, second_base, second_dir, pki, host=OTHER, fleet=pve.fleet)
            engine_guard.first_on_path(refusing_ssh(home))
            yield pve, nuc, first_dir, second_dir


def _current(name: str) -> None:
    where = paths.home(REPO)
    hosts.make_current(hosts.Layout(where), name, overrides.read(where, None).client.digest)


def test_each_host_is_read_with_its_own_api_token_and_settings_and_says_its_name(two) -> None:
    pve, nuc, _, _ = two
    where = paths.home(REPO)
    overrides.set(where, "vm_searchdomain", "nuc.internal", OTHER, overrides.read(where, OTHER).host.digest)
    for name, fleet, domain in ((HOST, pve, ""), (OTHER, nuc, "nuc.internal")):
        config = status_inputs.load(where, name, status_inputs.Opts(), environ=os.environ)
        other = OTHER if name == HOST else HOST
        assert config["fleet"]["url"] == f"https://127.0.0.1:{fleet.port}/api2/json"
        header = f"PVEAPIToken={ENGINE_USER}!{ENGINE_TOKEN}={fleet.secret}"
        assert config["fleet"]["headers"] == {"Authorization": header}
        assert config["fleet"]["settings"]["host_flag"] == f" --host {name}"
        assert config["fleet"]["hosts"] == ["localhost", name, other]
        assert config["hop"]["name"] == name
        assert config["vm"]["drift"]["searchdomain"] == domain
        assert config["vm"]["claude"]["login_command"].endswith(f" --host {name}")


def test_a_run_without_host_reads_the_current_one(two) -> None:
    _, nuc, _, _ = two
    where = paths.home(REPO)
    for name in (OTHER, HOST, OTHER):
        _current(name)
        assert status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)["hop"]["name"] == name
    current = status_inputs.load(where, None, status_inputs.Opts(), environ=os.environ)
    assert nuc.secret in current["fleet"]["headers"]["Authorization"]


def test_a_host_that_is_none_of_the_fleet_s_is_refused_before_anything_is_read(two) -> None:
    with pytest.raises(status_inputs.Refused) as refused:
        status_inputs.load(paths.home(REPO), "elsewhere", status_inputs.Opts(), environ=os.environ)
    assert "elsewhere" in refused.value.sentence


def test_aw_status_host_json_asks_that_host_s_api_alone(two, home, tmp_path) -> None:
    _, _, first_dir, second_dir = two
    # pve the current host: --host nuc reads nuc in its place.
    _current(HOST)
    before = len(_pve_requests(first_dir)), len(_pve_requests(second_dir))
    checkout = _checkout(tmp_path / "made", {"status": "cli.status:main"})
    words = ("status", "--host", OTHER, "--json")
    ran = run_launcher(*words, home=home, launcher=checkout / "aw", first_on_path=(refusing_ssh(home),), timeout=180)
    assert ran.returncode in (0, 1, 2), ran.stderr[-2000:]
    report = json.loads(ran.stdout)
    assert {"alpha", "beta", "gamma"} <= {vm["name"] for vm in report["vms"]}
    assert len(_pve_requests(first_dir)) == before[0]
    sent = _pve_requests(second_dir)[before[1] :]
    assert sent and {request["method"] for request in sent} == {"GET"}
