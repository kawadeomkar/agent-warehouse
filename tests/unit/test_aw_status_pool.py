"""aw status's reader of the fleet, the pool's members and their records (lib/claude_vm/cli/status_pool.py;
docs/architecture/engine.md §16.3), against the fake Proxmox on E4's fleet folder.

The reference is tools/status, which reads the fleet by its tags: run on the same fake, the VMs tagged, then the tags
taken off and the VMs read by aw status as the pool's members by their records, the two reports are the same but for
what DIFFERENT names, each with why. aw status sends GETs alone; a member with no record, or a copy's, is no VM of the
report and is said in a line; a job a record names that this computer has none of is said as another computer's, and a
lost one of its own as lost; a VM with no host key filed here is never dialled; a VM's unapplied change is "not known";
under proxmox_audit_scope: node each VM outside the pool is read for its disks alone, and only where the storage or
capacity section is asked for, so that nothing else of it reaches the report, the JSON or the log; under pool the node's
other VMs are not read at all and a line says so; every line names aw and parses back through the one check; and
nothing of the fleet's folder is written. What aw status imports is held by tests/unit/test_aw_cli_imports.py.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import pytest
from aw_kit import run_launcher
from aw_machine import machine, planted, ssh_stand_in
from fleet_fixture import HOST, recorded, refusing_ssh, whole_fleet
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _env, _pve, _pve_requests, _pve_vm, fake_fleet
from pki import proxmox_pki
from test_aw_cli_imports import _checkout
from test_status import status_command, status_root

from claude_vm.cli import parse, status_inputs, status_pool
from claude_vm.fleet import overrides, paths
from claude_vm.schema.words import VERBS
from claude_vm.status import model

# The fleet's VMs (harness.fake_fleet's): alpha and beta running, gamma stopped.
FLEET_VMS = {9010: "alpha", 9011: "beta", 9012: "gamma"}
# whole_fleet's VM on a node that is down, nameless, tagged as the fleet's, and a member of the pool too. Today's
# report makes it a row of the fleet by its tag; aw's never judges it, since its configuration cannot be read, so it is
# one "unread" line of its own and one of the node's other VMs, never a row: the comparison holds it apart so.
DOWN = (8001,)
LISTED = list(FLEET_VMS.values())
# The folder _aw's checkout is made under: long, as macOS's temporary folders are (lines.py names the program by it).
LONG = "a-checkout-at-a-long-path-" * 4
# The sections read through the API alone: no VM is dialled, so the two reports are the API's reads alone.
API_ONLY = "vms,storage,capacity"
# What differs between tools/status's report and aw status's, by key, each with why: dropped from both before they are
# compared.
DIFFERENT = {
    "now": "the moment each run read at",
    "read_at": "the moment each run read at",
    "title": "names the moment each run read at",
    "known_at": "aw's document alone has it: the run's own time, a control VM's age of what it shows",
    "unapplied": "aw's rows alone have it: an unapplied change, not known while the record keeps no digest (F28)",
    "tags": "the tags taken off for aw status, which reads none",
    "uptime": "grows between the two runs",
    "created_ago": "grows between the two runs",
    "built_ago": "grows between the two runs",
    "command": "the line a person types: today's just's, aw's printed whole by the printer (held to aw, and to the one"
    " check, by test_every_line_names_aw_and_parses_back_through_the_one_check)",
}
# The one line of each report the other has no place for, by its subject and words, each with why: today's on what
# of a fleet read by its tags is outside the pool, where aw's fleet is the pool; and aw's on the fake's template,
# judged by pve/template.py's judge (card E11), which refuses it, being no member of the pool with no record of this
# engine's build, where today's compares it with its settings alone.
TODAY_ALONE = ("proxmox", "which guests are members of pool")
AW_ALONE = ("template", "is no member of the pool")
# A VM outside the pool, under scope node, and what of its configuration would be found where more of it were kept
# than its disks' sizes.
OUTSIDE = (9013, "outsider")
OUTSIDE_MARK = "outside-notes-never-shown"
# The command's words, one verb of the table each, that a line's text may name after the program.
_VERB = "(?:" + "|".join(sorted(map(re.escape, VERBS))) + r")\b"


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made(home, tmp_path, pki, engine_guard):
    """E4's whole fleet against the fake, its VMs tagged and its pool's members with their records; a refusing ssh
    first on PATH from then on."""
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"])):
        fleet = whole_fleet(home, base, directory, pki)
        recorded(base, FLEET_VMS, down=DOWN)
        engine_guard.first_on_path(refusing_ssh(home))
        yield fleet


def _aw(made, home, tmp_path, *words, env=None, first=None):
    # A checkout of the test's own, made once a test, whose lib/ is this checkout's, which names status's handler. At a
    # long path, as macOS's temporary folders are: a line names the program by its path, so every command is longer
    # than a field's limit (status/model.py's FIELD_LIMIT), and must still be whole.
    where = tmp_path / LONG / "made"
    checkout = where / "checkout" if where.exists() else _checkout(where, {})
    on_path = (first,) if first is not None else (refusing_ssh(home),)
    return run_launcher(
        "status", *words, home=home, launcher=checkout / "aw", first_on_path=on_path, env=env, timeout=180
    )


def _report(ran) -> dict:
    assert ran.returncode in (0, 1, 2), ran.stderr[-2000:]
    assert "Traceback" not in ran.stderr, ran.stderr[-2000:]
    return json.loads(ran.stdout)


def _today(made, tmp_path, *words) -> subprocess.CompletedProcess:
    root = status_root(
        tmp_path,
        made.base,
        {
            "proxmox_api_user": ENGINE_USER,
            "proxmox_api_token_id": ENGINE_TOKEN,
            "vault_proxmox_api_token_secret": made.secret,
            "proxmox_node": "pve",
            "proxmox_pool": ENGINE_POOL,
            "proxmox_trust_file": str(made.home / "fleet" / "hosts" / HOST / "proxmox-trust.pem"),
        },
    )
    return subprocess.run(
        status_command(root, None, *words),
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _plain(value):
    """value with DIFFERENT's keys dropped, and each line's program named aw: today's just, and aw's path, the
    checkout's launcher the test runs."""
    if isinstance(value, dict):
        return {key: _plain(one) for key, one in value.items() if key not in DIFFERENT}
    if isinstance(value, list):
        return [_plain(one) for one in value]
    if isinstance(value, str):
        return re.sub(rf"(?:\bjust|\S*/checkout/aw) (?={_VERB})", "aw ", value)
    return value


def _untagged(made) -> None:
    for vmid in FLEET_VMS:
        _pve(made.base, "PUT", f"nodes/pve/qemu/{vmid}/config", delete="tags")


def _lines(report: dict) -> list[str]:
    """Every command the report names: each item's, and every *_command of the document."""
    found: list[str] = []

    def walk(value, key=""):
        if isinstance(value, dict):
            for inner_key, inner in value.items():
                walk(inner, inner_key)
        elif isinstance(value, list):
            for inner in value:
                walk(inner, key)
        elif isinstance(value, str) and (key == "command" or key.endswith("_command")) and value:
            found.append(value)

    walk(report)
    return found


def _tree(root: Path, leave: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and leave not in path.parents
    }


# ------------------------------------------------------------------------------------------ the comparison ----


def test_the_pool_and_its_records_give_today_s_report_of_the_tagged_fleet(made, home, tmp_path) -> None:
    # beta and gamma made two days ago, as alpha was (harness.fake_fleet): a VM made just now has a point of its day
    # more for each minute it lives, and the two reports are read moments apart, often a minute apart.
    for vmid in (9011, 9012):
        _pve(made.base, "PUT", "fake/disk-ctime", vmid=vmid, ctime=int(time.time()) - 2 * 86400)
    today = _today(made, tmp_path, "--json", "--only", API_ONLY)
    assert today.returncode in (0, 1, 2), today.stderr[-2000:]
    _untagged(made)
    new = _report(_aw(made, home, tmp_path, "--json", "--only", API_ONLY))
    old = json.loads(today.stdout)
    listed = [(vm.get("vmid"), vm.get("name"), vm.get("skipped")) for vm in old["vms"]]
    down = _held_apart(made, old, new)
    assert [vm["name"] for vm in new["vms"]] == [vm["name"] for vm in old["vms"]] == LISTED, listed
    assert sorted(new) == sorted([*old, status_pool.KNOWN_AT]) and down
    assert new[status_pool.KNOWN_AT] == new["now"]
    plain_old, plain_new = _plain(old), _plain(new)
    for plain, (subject, words) in ((plain_old, TODAY_ALONE), (plain_new, AW_ALONE)):
        alone = [item for item in plain["attention"] if item["vm"] == subject and words in item["text"]]
        assert len(alone) == 1, plain["attention"]
        plain["attention"].remove(alone[0])
    differing = {
        key: (plain_old.get(key), plain_new.get(key)) for key in plain_new if plain_old.get(key) != plain_new.get(key)
    }
    assert differing == {}, "\n".join(_where_they_part(plain_old, plain_new))[-4000:]
    assert new["exit"] == old["exit"]


def _where_they_part(old, new, at: str = "") -> list[str]:
    """Each place the two reports differ, by its path, with each side's value cut short."""
    if isinstance(old, dict) and isinstance(new, dict):
        keys = sorted({*old, *new})
        return [line for key in keys for line in _where_they_part(old.get(key), new.get(key), f"{at}.{key}")]
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        pairs = enumerate(zip(old, new, strict=True))
        return [line for i, (one, other) in pairs for line in _where_they_part(one, other, f"{at}[{i}]")]
    return [] if old == new else [f"{at}: today {str(old)[:300]} / aw {str(new)[:300]}"]


def _held_apart(made, old: dict, new: dict) -> list[dict]:
    """DOWN held apart, as its comment says: its row and today's line on it taken out of today's report, aw's line on
    it out of aw's, and it counted among today's other VMs, as aw counts it. Each line taken out, aw's first."""
    taken = []
    for vmid in DOWN:
        [entry] = [one for one in _pve(made.base, "GET", "cluster/resources", type="vm")["data"] if one["vmid"] == vmid]
        subject = f"VMID {vmid}"
        [said] = [item for item in new["attention"] if item["vm"] == subject]
        assert said["level"] == "unread" and "on a node that is down" in said["text"], said
        assert not [vm for vm in new["vms"] if vm.get("vmid") == vmid], new["vms"]
        old["vms"] = [vm for vm in old["vms"] if vm.get("vmid") != vmid]
        today = [item for item in old["attention"] if item["vm"] == subject]
        new["attention"].remove(said)
        for item in today:
            old["attention"].remove(item)
        others = old["others"]
        others["count"] += 1
        others["vcpus"] += int(entry.get("maxcpu") or 0)
        others["mem"] += int(entry.get("maxmem") or 0)
        others["running"] += entry.get("status") == "running"
        others["nodes"] = sorted({*others["nodes"], entry["node"]})
        taken += [said, *today]
    return taken


def test_a_member_on_a_node_that_is_down_is_one_unread_line_and_never_a_vm_of_the_report(made, home, tmp_path) -> None:
    # 8000 and 8001 on pve2, which is down: neither is read, so neither is known to be this fleet's; 8000 has no tag
    # and no record, which no reader could tell, its node being down.
    _pve(made.base, "PUT", "pools", poolid=ENGINE_POOL, vms="8000")
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    assert [vm["name"] for vm in report["vms"]] == LISTED
    assert not [vm for vm in report["vms"] if vm.get("vmid") in (8000, *DOWN)]
    for vmid in (8000, *DOWN):
        said = [item for item in report["attention"] if item["vm"] == f"VMID {vmid}"]
        assert len(said) == 1 and said[0]["level"] == "unread", report["attention"]
        assert "on a node that is down" in said[0]["text"] and "not known to be this fleet's" in said[0]["text"]


def test_every_request_is_a_get_and_nothing_of_the_fleet_s_folder_is_written(made, home, tmp_path) -> None:
    where = paths.home(REPO)
    before_tree = _tree(where.root, paths.status_cache(where))
    before = len(_pve_requests(tmp_path / "fake"))
    report = _report(_aw(made, home, tmp_path, "--json"))
    sent = _pve_requests(tmp_path / "fake")[before:]
    assert sent and {request["method"] for request in sent} == {"GET"}
    assert _tree(where.root, paths.status_cache(where)) == before_tree
    # Each VM's unapplied change is not known while its record keeps no digest it converged to.
    assert [vm["unapplied"] for vm in report["vms"]] == [status_pool.NOT_KNOWN] * len(LISTED)


def test_every_line_names_aw_and_parses_back_through_the_one_check(made, home, tmp_path) -> None:
    # Each line the model and the drawing name, printed whole for one host and for a host of several, read back by the
    # one check as the words it was printed from (the code form's placeholders aside, which name no value).
    for host in ("", "nuc"):
        for key, line in status_inputs.lines(host).items():
            words = shlex.split(line)
            assert words[0] == "aw" and (not host or "--host" in words or words[1] == "help"), line
            if key != "code_form":
                assert parse.read(words[1:]).verb == words[1], line
    # A report of a fleet with every kind of line this reader adds: none names just, and each command that names the
    # program names aw (the printer's line, made whole by the printer: tests/unit/test_aw_cli_printer.py).
    _pve_vm(made.base, 9013, "delta", tags="agent-warehouse")
    _pve(made.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9013")
    recorded(made.base, (9012,), job="0123456789abcdef")
    ran = _aw(made, home, tmp_path, "--json")
    report = _report(ran)
    assert not re.search(rf"\bjust {_VERB}", ran.stdout), ran.stdout[-2000:]
    named = [command for command in _lines(report) if re.search(rf"\baw {_VERB}", command)]
    assert any("aw destroy delta" in command for command in named), named
    assert any("aw logs 0123456789abcdef" in command for command in named), named


# ------------------------------------------------------------------------------------------ the members ----


def test_a_member_with_no_record_is_not_the_fleet_s_and_is_said_with_the_line_that_deletes_it(
    made, home, tmp_path
) -> None:
    # Tagged as today's were, a member of the pool, and no record: never a VM of the report.
    _pve_vm(made.base, 9013, "delta", tags="agent-warehouse")
    _pve(made.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9013")
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    assert [vm["name"] for vm in report["vms"]] == LISTED
    [item] = [item for item in report["attention"] if item["vm"] == "delta"]
    assert item["level"] == "todo"
    assert item["text"].startswith(f"it is a member of pool {ENGINE_POOL} with no record of this engine's")
    assert "aw destroy delta --confirm delta --set destroy_allow_unrecorded=true" in item["command"]


def test_a_copy_s_record_is_said_in_the_rule_s_own_words_and_a_vm_outside_the_pool_is_no_vm_of_it(
    made, home, tmp_path
) -> None:
    # alpha's record given to a clone of it, which the pool holds: a copy; and epsilon, tagged, outside the pool.
    alpha = _pve(made.base, "GET", "nodes/pve/qemu/9010/config")["data"]
    _pve_vm(made.base, 9013, "delta", tags="agent-warehouse", description=alpha["description"])
    _pve(made.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9013")
    _pve_vm(made.base, 9014, "epsilon", tags="agent-warehouse")
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    assert [vm["name"] for vm in report["vms"]] == LISTED
    [item] = [item for item in report["attention"] if item["vm"] == "delta"]
    assert item["level"] == "problem" and "copy" in item["text"] and "never changed or deleted" in item["text"]
    assert report["exit"] == 2 or report["counts"]["problem"] >= 1
    assert not [item for item in report["attention"] if item["vm"] == "epsilon"]


def test_two_members_that_carry_one_uuid_are_each_refused_as_twins(made, home, tmp_path) -> None:
    # delta given alpha's SMBIOS UUID and its record, as a restore without unique leaves them: neither is the fleet's
    # to change, since nothing tells them apart.
    alpha = _pve(made.base, "GET", "nodes/pve/qemu/9010/config")["data"]
    _pve_vm(made.base, 9013, "delta", description=alpha["description"], smbios1=alpha["smbios1"])
    _pve(made.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9013")
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    assert [vm["name"] for vm in report["vms"]] == [name for name in LISTED if name != "alpha"]
    for name, other in (("alpha", 9013), ("delta", 9010)):
        said = [item for item in report["attention"] if item["vm"] == name and "carries one UUID" in item["text"]]
        assert said and said[0]["level"] == "problem" and f"with VMID {other}" in said[0]["text"], report["attention"]


def test_a_record_s_job_that_is_no_job_id_is_a_problem_line_and_never_ends_the_run(made, home, tmp_path) -> None:
    # Anyone who may set gamma's notes may write its record's job: one that is no job's ID names no logs line.
    recorded(made.base, (9012,), job="not-a-job")
    ran = _aw(made, home, tmp_path, "--json", "--only", "vms")
    report = _report(ran)
    # Never 3, a failed run: 0 or 1 by the fleet's own lines, or 2 where the one unread line is DOWN's (a node down).
    unread = {item["vm"] for item in report["attention"] if item["level"] == "unread"}
    assert ran.returncode == report["exit"] and (ran.returncode in (0, 1) or unread == {f"VMID {v}" for v in DOWN})
    [item] = [item for item in report["attention"] if item["vm"] == "gamma" and "no job's ID" in item["text"]]
    assert item["level"] == "problem" and "not-a-job" in item["text"] and not item["command"]
    assert [vm["name"] for vm in report["vms"]] == LISTED


def test_a_template_that_carries_a_vm_s_uuid_makes_it_a_twin_as_the_engine_does(made, home, tmp_path) -> None:
    # delta, given alpha's SMBIOS UUID and its record and made a template, a member of the pool, as a restore without
    # unique and a convert leave it: the engine's reader refuses alpha as a twin (engine/fleet.py's _members), and so
    # does status's, by the one function both call.
    alpha = _pve(made.base, "GET", "nodes/pve/qemu/9010/config")["data"]
    _pve_vm(made.base, 9013, "delta", description=alpha["description"], smbios1=alpha["smbios1"])
    _pve(made.base, "POST", "nodes/pve/qemu/9013/template")
    _pve(made.base, "PUT", "pools", poolid=ENGINE_POOL, vms="9013")
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    assert [vm["name"] for vm in report["vms"]] == [name for name in LISTED if name != "alpha"]
    said = [item for item in report["attention"] if item["vm"] == "alpha" and "carries one UUID" in item["text"]]
    assert said and said[0]["level"] == "problem" and "with VMID 9013" in said[0]["text"], report["attention"]
    assert not [item for item in report["attention"] if item["vm"] == "delta"]
    for module in ("lib/claude_vm/cli/status_pool.py", "lib/claude_vm/engine/fleet.py"):
        assert "ownership.twins(" in (REPO / module).read_text(), module


def test_a_job_another_computer_left_on_a_vm_is_said_with_its_logs_line(made, home, tmp_path) -> None:
    job = "0123456789abcdef"
    recorded(made.base, (9012,), job=job)
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    [item] = [item for item in report["attention"] if item["vm"] == "gamma" and job in item["text"]]
    assert "a run on another computer left" in item["text"]
    assert f"aw logs {job}" in item["command"] and item["level"] == "todo"


def test_a_lost_job_of_this_computer_s_is_said_as_lost(monkeypatch, tmp_path) -> None:
    reader = status_pool.Pool(_given(tmp_path))
    reader.read[9010] = status_pool._Read({"vmid": 9010, "name": "alpha"}, {"job": "fedcba9876543210"})
    monkeypatch.setattr(status_pool.jobs, "find", lambda folder, job: object())
    monkeypatch.setattr(status_pool.jobs, "state", lambda found: "lost")
    [item] = reader._jobs([{"vmid": 9010, "name": "alpha"}])
    assert item["level"] == "problem" and "(lost)" in item["text"]
    assert item["command"].startswith("aw logs fedcba9876543210")
    monkeypatch.setattr(status_pool.jobs, "state", lambda found: "succeeded")
    assert reader._jobs([{"vmid": 9010, "name": "alpha"}]) == []


def test_a_vm_with_no_host_key_filed_here_is_never_dialled(made, home, tmp_path) -> None:
    # alpha a stand-in machine its agent reports, its record naming its identity, and no key of it filed here.
    alpha = machine(tmp_path / "machines", "alpha")
    planted(made.base, 9010, alpha)
    recorded(made.base, (9010,))
    ran = _aw(made, home, tmp_path, "--json", first=ssh_stand_in(tmp_path / "ssh", [alpha]))
    report = _report(ran)
    running = [vm["name"] for vm in report["vms"] if vm["status"] == "running"]
    assert running == ["alpha", "beta"]
    # Its way on names the program by the checkout's long path, past a field's limit, and is kept whole.
    assert len(f"{tmp_path / LONG}/made/checkout/aw deploy alpha") > model.FIELD_LIMIT
    for name in running:
        said = [item for item in report["attention"] if item["vm"] == name and "no host key" in item["text"]]
        assert said and said[0]["level"] == "unread", report["attention"]
        assert f"aw deploy {name} --only provision" in said[0]["command"], said
    assert alpha.calls() == []


# ------------------------------------------------------------------------------------------ the scopes ----


def test_under_scope_node_a_vm_outside_the_pool_is_read_for_its_disks_alone(made, home, tmp_path) -> None:
    vmid, name = OUTSIDE
    _pve_vm(made.base, vmid, name, ciuser=OUTSIDE_MARK, description=OUTSIDE_MARK)
    # Its disk on local-lvm, which the token's listing of the storage leaves out (it holds no VM.Config.Disk on it).
    config = _pve(made.base, "GET", f"nodes/pve/qemu/{vmid}/config")["data"]
    size = sum(bytes_ for storage, bytes_ in status_pool.disks(config) if storage == "local-lvm")
    sent = len(_pve_requests(tmp_path / "fake"))
    _report(_aw(made, home, tmp_path, "--json", "--only", "vms"))
    asked = [request["path"] for request in _pve_requests(tmp_path / "fake")[sent:]]
    assert not [path for path in asked if path.endswith(f"/qemu/{vmid}/config")]
    log = tmp_path / "status.log"
    ran = _aw(made, home, tmp_path, "--json", "--only", "storage", env={"ANSIBLE_LOG_PATH": str(log)})
    report = _report(ran)
    asked = [request["path"] for request in _pve_requests(tmp_path / "fake")[sent:]]
    assert [path for path in asked if path.endswith(f"/qemu/{vmid}/config")]
    [storage] = [one for one in report["storages"] if one.get("storage") == "local-lvm"]
    assert storage["counted"] == status_pool.FLOOR
    assert size > 0 and storage["provisioned"] - storage["ours"] >= size, (size, storage)
    for text in (ran.stdout, ran.stderr, log.read_text() if log.exists() else ""):
        assert OUTSIDE_MARK not in text


def test_under_scope_pool_the_node_s_other_vms_are_not_read_and_a_line_says_so(made, home, tmp_path) -> None:
    vmid, name = OUTSIDE
    _pve_vm(made.base, vmid, name)
    where = paths.home(REPO)
    overrides.set(where, "proxmox_audit_scope", "pool", HOST, overrides.read(where, HOST).host.digest)
    sent = len(_pve_requests(tmp_path / "fake"))
    report = _report(_aw(made, home, tmp_path, "--json", "--only", "vms,storage,capacity"))
    asked = [request["path"] for request in _pve_requests(tmp_path / "fake")[sent:]]
    assert not [path for path in asked if f"/qemu/{vmid}/" in path]
    assert report["others"] == status_pool.NOTHING
    assert {one.get("counted") for one in report["storages"]} == {status_pool.POOL_ALONE}
    [item] = [item for item in report["attention"] if item["vm"] == "proxmox" and "outside pool" in item["text"]]
    assert "proxmox_audit_scope: pool" in item["text"]


# ------------------------------------------------------------------------------------------ in this process ----


def _given(tmp_path: Path) -> dict:
    from claude_vm.pve import template
    from claude_vm.schema.words import Line

    line = Line("deploy", {"only": "template"})
    base = template.Base("agent-warehouse-template", 9000, "pve", ENGINE_POOL, False, line)
    return {
        "host": HOST,
        "host_flag": "",
        "pool": ENGINE_POOL,
        "scope": "node",
        "base": base,
        "want": {},
        "custom": False,
        "known_hosts": str(tmp_path / "known_hosts"),
        "jobs": str(tmp_path / "jobs"),
    }


def test_a_vm_s_disks_are_its_disk_keys_sizes_on_a_storage_and_nothing_else() -> None:
    config = {
        "scsi0": "local-lvm:vm-9013-disk-0,size=32G",
        "virtio1": "fast:vm-9013-disk-1,size=512M",
        "ide2": "local:iso/ubuntu.iso,media=cdrom,size=2G",
        "efidisk0": "local-lvm:vm-9013-disk-2,size=4M",
        "scsi3": "local-lvm:vm-9013-disk-3",
        "description": "scsi9: local-lvm:x,size=1T",
        "unused0": "local-lvm:vm-9013-disk-9",
    }
    assert status_pool.disks(config) == [
        ("local-lvm", 32 * 1024**3),
        ("fast", 512 * 1024**2),
        ("local-lvm", 4 * 1024**2),
    ]


def test_the_reader_ranks_its_lines_as_the_model_does_and_ends_with_its_exit(tmp_path) -> None:
    assert status_pool.LEVELS == model.LEVELS
    reader = status_pool.Pool(_given(tmp_path))
    reader.said = [status_pool._item("problem", "delta", "a copy")]
    run = type("Run", (), {"settings": {"tag": "agent-warehouse"}})()
    report = {
        "attention": [
            status_pool._item("todo", "alpha", "no VM tagged agent-warehouse is named omega"),
            status_pool._item("todo", "beta", "sign in"),
        ],
        "vms": [{"name": "alpha"}, {"name": "beta"}],
        "now": 1.5,
        "exit": 0,
    }
    done = reader.report(run, report)
    assert [item["level"] for item in done["attention"]] == ["problem", "todo", "todo"]
    assert done["attention"][1]["text"] == f"no VM of pool {ENGINE_POOL} is named omega"
    counts = {"unread": 0, "problem": 1, "todo": 2}
    assert (done["exit"], done["counts"], done[status_pool.KNOWN_AT]) == (1, counts, 1.5)


def test_the_reader_imports_nothing_the_entry_may_not() -> None:
    # G15's boundary, by the module's own imports: none of the engine's workflows, today's status, pydantic, Ansible
    # or the MCP SDK (test_aw_cli_imports.py holds what a run of aw status loads).
    tree = ast.parse((REPO / "lib" / "claude_vm" / "cli" / "status_pool.py").read_text())
    named = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    named |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    refused = ("claude_vm.engine", "claude_vm.status", "pydantic", "ansible", "mcp")
    assert [name for name in named if name.startswith(refused)] == []
    assert os.path.basename(status_pool.__file__) == "status_pool.py"
