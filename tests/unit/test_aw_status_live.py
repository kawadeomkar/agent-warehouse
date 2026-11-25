"""aw status run whole against the fake Proxmox, on E4's fleet folder (lib/claude_vm/cli/status.py; docs/architecture/
engine.md §16.3): the new path's cases of tests/unit/test_status_live.py, which stays as it is and holds tools/status.

`aw status --json`, through the launcher of a checkout the test makes whose table gives status its handler (as card G16
gives it in the repository's own), prints a report with today's keys and today's VMs: tools/status --json, run against
the same fake as test_status.py runs it (status_root) and told what the fleet's host is, is the reference. A Ctrl-C at
the main thread while read_fleet waits - on a VM's probe, or on the API's answers its probe waits for - ends the run
within a slice of the wait (status/engine.py's _waited), never once the awaited call returns: sent as
`_thread.interrupt_main` sends it, which wakes no wait of a lock on any system, as a signal wakes none on macOS, and
only once the main thread is seen waiting there, so that it never lands in code that runs before the wait. Every run
here has a refusing ssh first on PATH, and keeps nothing outside the test's own directory.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from aw_kit import run_launcher
from fleet_fixture import HOST, refusing_ssh, whole_fleet
from harness import ENGINE_POOL, ENGINE_TOKEN, ENGINE_USER, REPO, _env, fake_fleet
from pki import proxmox_pki
from test_aw_cli_imports import _checkout
from test_status import status_command, status_root

# A run of aw status's handler in a Python of its own, from the fleet the test made: the call awaited by the wait named
# by argv[2] made to last ten seconds, and a Ctrl-C sent, as interrupt_main sends one, once the main thread is seen
# waiting there on a lock - read_fleet's line the same 0.3 s apart, below reach_each for an answer; for a probe, once
# fill has returned, so past the waits on the API's answers, which are each a slice long and would take the Ctrl-C
# first - and never sooner. Says how long the Ctrl-C took to land.
CTRL_C = r"""
import _thread, signal, sys, threading, time
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from claude_vm.cli import status
from claude_vm.status import engine

signal.signal(signal.SIGINT, signal.default_int_handler)
waiting = threading.Event()
past = threading.Event()
sent = []
fill = engine.Run.fill
answer = engine.Proxmox.get


def probe(self, *args, **kwargs):
    waiting.set()
    time.sleep(10)


def filled(self, *args, **kwargs):
    whole = fill(self, *args, **kwargs)
    past.set()
    return whole


def get(self, item):
    if isinstance(item, dict) and item.get("kind") == "network":
        waiting.set()
        time.sleep(10)
    return answer(self, item)


MAIN = threading.main_thread().ident
WHERE = {
    "probe": lambda names: not {"reach_each", "fill"} & names,
    "answer": lambda names: "reach_each" in names,
}


# read_fleet's line and the functions on the main thread's stack while it waits on a lock where argv[2] names; None
# while it runs, or waits anywhere else.
def blocked():
    frame = sys._current_frames().get(MAIN)
    if frame is None or not frame.f_code.co_filename.endswith("threading.py"):
        return None
    names, line = set(), None
    while frame is not None:
        names.add(frame.f_code.co_name)
        line = frame.f_lineno if frame.f_code.co_name == "read_fleet" else line
        frame = frame.f_back
    return (line, frozenset(names)) if line is not None and WHERE[sys.argv[2]](names) else None


def interrupt():
    waiting.wait(120)
    if sys.argv[2] == "probe":
        past.wait(120)
    seen, deadline = None, time.monotonic() + 60
    while time.monotonic() < deadline:
        now = blocked()
        if now is not None and now == seen:
            break
        seen = now
        time.sleep(0.3)
    sent.append(time.monotonic())
    _thread.interrupt_main()


ended = engine._end_for_an_interrupt


def landed(screen):
    print(f"landed after {time.monotonic() - sent[0]:.2f} s", file=sys.stderr, flush=True)
    ended(screen)


if sys.argv[2] == "probe":
    engine.Run.reach_vm = probe
    engine.Run.fill = filled
else:
    engine.Proxmox.get = get
engine._end_for_an_interrupt = landed
threading.Thread(target=interrupt, daemon=True).start()
sys.exit(status.main(SimpleNamespace(set=()), json=True))
"""


@pytest.fixture
def pki(tmp_path):
    return proxmox_pki(tmp_path / "pki", key_usage=True)


@pytest.fixture
def made(home, tmp_path, pki, engine_guard):
    """E4's whole fleet against the fake (alpha and beta running, gamma stopped), its route pinned direct; a refusing
    ssh first on PATH from then on."""
    directory = tmp_path / "fake"
    directory.mkdir()
    for base in fake_fleet(directory, engine=True, certificate=(pki["cert"], pki["key"])):
        fleet = whole_fleet(home, base, directory, pki)
        engine_guard.first_on_path(refusing_ssh(home))
        yield fleet


def test_aw_status_json_against_the_fake_prints_today_s_report(made, home, tmp_path) -> None:
    # tools/status as test_status.py runs it (status_root: the managed SSH config, ssh and its sockets the test's own),
    # told what init tells the fleet's host: the token, the node, the pool and the anchor the host keeps.
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
    today = subprocess.run(
        status_command(root, None, "--json"),
        cwd=root,
        env=_env(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert today.returncode in (0, 1, 2), today.stderr[-2000:]
    checkout = _checkout(tmp_path / "made", {"status": "cli.status:main"})
    refusing = (refusing_ssh(home),)
    ran = run_launcher("status", "--json", home=home, launcher=checkout / "aw", first_on_path=refusing, timeout=180)
    assert ran.returncode == today.returncode, ran.stderr[-2000:]
    old, new = json.loads(today.stdout), json.loads(ran.stdout)
    assert sorted(new) == sorted(old)
    assert [vm["name"] for vm in new["vms"]] == [vm["name"] for vm in old["vms"]]
    assert {"alpha", "beta", "gamma"} <= {vm["name"] for vm in new["vms"]}
    assert [vm["state"] for vm in new["vms"]] == [vm["state"] for vm in old["vms"]]


def test_a_word_refused_ends_the_run_as_a_read_that_failed_before_anything_is_sent(made, home, tmp_path) -> None:
    calls = tmp_path / "fake" / "pve" / "calls.log"
    before = calls.read_bytes()
    checkout = _checkout(tmp_path / "made", {"status": "cli.status:main"})
    words = ("status", "--set", "vm_cores=unit-test-given")
    ran = run_launcher(*words, home=home, launcher=checkout / "aw", first_on_path=(refusing_ssh(home),))
    assert ran.returncode == 3, ran.stderr[-2000:]
    assert ran.stderr.startswith("error: ") and "vm_cores" in ran.stderr
    assert "unit-test-given" not in ran.stderr + ran.stdout
    assert ran.stdout == ""
    assert calls.read_bytes() == before


@pytest.mark.parametrize("wait", ["probe", "answer"])
def test_a_ctrl_c_while_read_fleet_waits_ends_the_run_within_a_slice(made, tmp_path, wait) -> None:
    # The awaited call lasts ten seconds; the Ctrl-C lands within a slice of the wait (0.1 s), not once it returns.
    done = subprocess.run(
        [sys.executable, "-I", "-c", CTRL_C, str(REPO / "lib"), wait],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 3, done.stderr[-2000:]
    assert "interrupted" in done.stderr
    [after] = re.findall(r"landed after ([0-9.]+) s", done.stderr)
    assert float(after) < 3, done.stderr[-2000:]
