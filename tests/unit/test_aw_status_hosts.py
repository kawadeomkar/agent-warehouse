"""aw status against one host of a fleet of two (lib/claude_vm/cli/status.py and status_inputs.py; docs/architecture/
engine.md §16.3): the new path's cases of tests/unit/test_status_hosts.py, which stays as it is and holds today's
status.

The fleet is E4's folder with a second host, nuc, set up by init against a fake Proxmox of its own, each with a token of
its own. A run reads the host --host names, else the current one (client.yml's current_host), and nothing of the other:
its API, its token and its settings are the host's own, every command a line names ends with ` --host <host>`, and the
other host's name is one no VM may be named after. `aw status --host nuc --json` sends its GETs to nuc's fake alone,
none to pve's. Reading every host at once, a child each, as today's status does, is not built (card G16's Notes): it
needs the table's word that starts the child.
"""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace

import pytest
from aw_kit import run_launcher
from fleet_fixture import HOST, recorded, refusing_ssh, whole_fleet
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _pve, _pve_requests, _pve_vm, fake_fleet
from pki import proxmox_pki
from test_aw_cli_imports import _checkout

from claude_vm.cli import status_inputs
from claude_vm.fleet import hosts, overrides, paths
from claude_vm.schema.words import VERBS
from claude_vm.status import engine
from claude_vm.status.model import FIELD_LIMIT, status_clean
from claude_vm.text import one_line

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
        recorded(first_base, (9010, 9011, 9012))
        for second_base in fake_fleet(second_dir, engine=True, certificate=(pki["cert"], pki["key"])):
            nuc = whole_fleet(home, second_base, second_dir, pki, host=OTHER, fleet=pve.fleet)
            recorded(second_base, (9010, 9011, 9012))
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


# The verbs whose lines take --host, by the table: each such line a report names, of a fleet of two hosts, names the
# host it was read from (`logs` and `help` take none), at its end but where the printer puts it before a form's
# placeholders (the code form's).
_HOSTED = sorted(name for name, verb in VERBS.items() if any(option.long == "host" for option in verb.options))
_NAMED = re.compile(rf"\baw ({'|'.join(map(re.escape, _HOSTED))})\b")


def _lines(text: str) -> list[str]:
    """Each line of an aw verb that takes --host, in text whose whitespace is made one space (the text report wraps a
    line at its width): from the program's name to the next one's, or to a sentence's "(" after it."""
    flat = " ".join(text.split())
    starts = [found.start() for found in re.finditer(r"\baw ", flat)] + [len(flat)]
    return [
        re.split(r" \(| · ", flat[start:end])[0].rstrip(" ,.;:")
        for start, end in zip(starts, starts[1:], strict=False)
        if _NAMED.match(flat, start)
    ]


def _commands(value, key=""):
    """Every command the JSON document names: each item's command and every *_command, wherever it is."""
    if isinstance(value, dict):
        return [found for inner_key, inner in value.items() for found in _commands(inner, inner_key)]
    if isinstance(value, list):
        return [found for inner in value for found in _commands(inner, key)]
    return [value] if isinstance(value, str) and value and (key == "command" or key.endswith("_command")) else []


def test_every_line_of_a_report_of_the_second_host_ends_with_its_host(two, home, tmp_path) -> None:
    # On nuc: delta a member with no record, whose line deletes it; alpha and beta running with no host key filed
    # here, whose line files it; and the model's own lines.
    _, nuc, _, _ = two
    _pve_vm(nuc.base, 9013, "delta")
    _pve(nuc.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9013")
    _current(HOST)
    # The checkout at a long path: a line names the program by the path it was started by, so every command of the
    # report is longer than a field's limit (status/model.py's FIELD_LIMIT), and its --host, which comes last, is kept.
    checkout = _checkout(tmp_path / ("a-checkout-at-a-long-path-" * 4), {"status": "cli.status:main"})
    assert len(f"{checkout}/aw destroy delta") > FIELD_LIMIT
    named = []
    for words in (("status", "--host", OTHER, "--json"), ("status", "--host", OTHER)):
        first = (refusing_ssh(home),)
        ran = run_launcher(*words, home=home, launcher=checkout / "aw", first_on_path=first, timeout=180)
        assert ran.returncode in (0, 1, 2), ran.stderr[-2000:]
        texts = _commands(json.loads(ran.stdout)) if "--json" in words else [ran.stdout]
        lines = [line for text in texts for line in _lines(text)]
        assert lines and [line for line in lines if f" --host {OTHER} " not in f"{line} "] == [], lines
        named += lines
    for wanted in ("aw destroy delta", "aw deploy alpha --only provision", "aw deploy beta --only provision"):
        assert sum(wanted in line for line in named) >= 2, (wanted, named)


def test_aw_s_printed_lines_and_commands_are_kept_whole_and_today_s_commands_cut_as_before() -> None:
    # A command from a checkout at a long path: aw's run keeps it whole, today's cuts it at a field's limit, as before;
    # any other field is cut on both roads, and aw's printed lines (settings' "lines") are kept whole.
    long = f"/{'a-long-path/' * 12}aw deploy alpha --only configure --host nuc"
    assert len(long) > FIELD_LIMIT
    vm, cut = {"configure_command": long, "name": long}, one_line(long, FIELD_LIMIT)
    opts, screen = SimpleNamespace(cache=None), SimpleNamespace(sections=["vms"])
    config = {"fleet": {"settings": {}}, "run": {"names": []}}
    assert engine.Run(opts, screen, config, program="aw").clean(vm) == {"configure_command": long, "name": cut}
    assert engine.Run(opts, screen, config).clean(vm) == {"configure_command": cut, "name": cut}
    assert status_clean({"lines": {"reconfigure": long}}) == {"lines": {"reconfigure": long}}
