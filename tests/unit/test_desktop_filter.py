"""Unit tests for lib/claude_vm/desktop.py, the filters of filter_plugins/desktop.py: the argv that opens a sign-in page
on this machine, and the run waiting here for a sign-in, as a VM records it and this machine is asked about it."""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys

import pytest
from harness import load_module

from claude_vm import desktop as desktop_filters

REPO = pathlib.Path(__file__).resolve().parents[2]
page_opener = desktop_filters.page_opener

URL = "https://github.com/login/device"


@pytest.mark.parametrize(
    ("browser", "argv"),
    [
        ("firefox", ["firefox", URL]),
        # The documented form: a list of commands. Only the first can be an argv.
        ("firefox:chromium", ["firefox", URL]),
        ("firefox --new-tab %s:chromium", ["firefox", "--new-tab", URL]),
        # %s is where the URL goes, inside an argument too, and then not appended.
        ("firefox --new-tab %s", ["firefox", "--new-tab", URL]),
        ("browser --url=%s --fresh", ["browser", "--url=" + URL, "--fresh"]),
        # Split as a shell would: no empty argument for a double space, and a
        # quoted path with a space stays one argument.
        ("firefox  --new-tab", ["firefox", "--new-tab", URL]),
        ("'/Applications/My Browser.app/run' %s", ["/Applications/My Browser.app/run", URL]),
        # An empty first entry is no command: the next one is the first.
        (":chromium", ["chromium", URL]),
        ("  chromium  ", ["chromium", URL]),
    ],
)
def test_browser_names_the_command(browser, argv):
    assert page_opener(URL, browser, ["/usr/bin/open", "/usr/bin/xdg-open"]) == argv


@pytest.mark.parametrize(
    ("found", "argv"),
    [
        (["/usr/bin/open"], ["open", URL]),
        (["/usr/bin/xdg-open"], ["xdg-open", URL]),
        # macOS's opener first when both are there.
        (["/usr/bin/xdg-open", "/usr/bin/open"], ["open", URL]),
        # Names as well as paths, and which's line endings.
        (["xdg-open\n"], ["xdg-open", URL]),
        # pbcopy and the like are no openers.
        (["/usr/bin/pbcopy", "/usr/bin/xclip"], []),
        ([], []),
        (None, []),
    ],
)
def test_without_browser_an_opener_that_was_found_is_used(found, argv):
    assert page_opener(URL, "", found) == argv


@pytest.mark.parametrize("browser", [None, "", " ", ":", "::"])
def test_a_browser_that_names_no_command_falls_back(browser):
    assert page_opener(URL, browser, ["/usr/bin/open"]) == ["open", URL]


def test_a_browser_that_cannot_be_split_falls_back():
    # Unbalanced quotes read no command; the openers are tried instead.
    assert page_opener(URL, "'firefox", ["/usr/bin/xdg-open"]) == ["xdg-open", URL]
    assert page_opener(URL, "'firefox", []) == []


def test_the_url_is_one_argument_whatever_it_holds():
    # Never handed to a shell: a URL with spaces or quotes is still one argument.
    url = "https://example.com/a b?c='d'"
    assert page_opener(url, "firefox", []) == ["firefox", url]
    assert page_opener(url, "", ["/usr/bin/open"]) == ["open", url]


@pytest.mark.parametrize(
    "browser",
    [
        "sh -c 'firefox %s'",
        "/bin/bash -c 'open \"$1\"' _",
        "zsh -c 'firefox %s'",
        "fish -c 'firefox %s'",
        # WSL's usual ways to Windows' browser, which read the URL as a command line again.
        "/mnt/c/Windows/System32/cmd.exe /c start",
        "cmd.exe /c start %s",
        "powershell.exe -NoProfile -Command Start-Process",
        "pwsh -c Start-Process %s",
        "CMD.EXE /c start",
        # Through env, its options and assignments, and the launchers that run a command given.
        "env sh -c 'firefox %s'",
        "/usr/bin/env -i LANG=C bash -c 'firefox %s'",
        "env -u FOO sh -c 'firefox %s'",
        "env -C /tmp bash -c 'firefox %s'",
        "env -S 'sh -c' 'firefox %s'",
        "nohup sh -c 'firefox %s'",
        "sudo -u me sh -c 'firefox %s'",
    ],
)
def test_a_browser_that_is_a_command_interpreter_opens_nothing(browser):
    # A page's query string - in a page the VM chose - would be read as a
    # command line here: the page is printed for the person instead, and no
    # opener is tried, since BROWSER said which program opens pages.
    page = "https://claude.ai/oauth/authorize?a=1&touch /tmp/x;b=2"
    assert page_opener(page, browser, ["/usr/bin/open", "/usr/bin/xdg-open"]) == []


@pytest.mark.parametrize(
    ("browser", "argv"),
    [
        # A program whose name only starts like one is a browser.
        ("shelly-browser %s", ["shelly-browser", URL]),
        ("env firefox", ["env", "firefox", URL]),
        ("/usr/bin/env PROFILE=work firefox %s", ["/usr/bin/env", "PROFILE=work", "firefox", URL]),
        ("nohup firefox %s", ["nohup", "firefox", URL]),
        # Only the words before the page can run it; one after it is the browser's argument.
        ("firefox %s --profile sh", ["firefox", URL, "--profile", "sh"]),
    ],
)
def test_a_browser_that_only_looks_like_one_still_opens(browser, argv):
    assert page_opener(URL, browser, []) == argv


def test_the_filters_are_registered():
    # filter_plugins/desktop.py is the adapter; test_claude_vm.py holds each of its filters to the core's function.
    adapter = load_module("filter_plugins/desktop.py", "desktop_adapter", register=False)
    assert set(adapter.FilterModule().filters()) == {"page_opener", "controller_run", "controller_run_state"}


# ------------------------------------------------- the run waiting for a sign-in
controller_run = desktop_filters.controller_run
controller_run_state = desktop_filters.controller_run_state


def _process():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _ended():
    """The pid of a process that has ended, and its record, made while it ran."""
    process = _process()
    try:
        return process.pid, desktop_filters._record(process.pid)
    finally:
        process.kill()
        process.wait()


def test_a_run_records_this_machine_and_its_own_process():
    # Here, outside a worker multiprocessing forked, the run's process is this one.
    run = controller_run(None)
    assert run["pid"] == os.getpid() and run["host"] == socket.gethostname(), run
    assert run["boot"] and run["start"], run
    assert json.loads(json.dumps(run)) == run


def test_a_run_whose_process_is_still_that_one_is_running():
    process = _process()
    try:
        record = json.dumps(desktop_filters._record(process.pid))
        assert controller_run_state(record) == {"state": "running", "host": socket.gethostname(), "pid": process.pid}
    finally:
        process.kill()
        process.wait()


def test_a_run_whose_process_ended_is_gone():
    pid, record = _ended()
    assert controller_run_state(json.dumps(record)) == {"state": "gone", "host": socket.gethostname(), "pid": pid}


def test_a_pid_another_process_has_since_is_a_run_gone():
    # The pid alone could be anyone's by now: only the start it was recorded with is that run.
    record = desktop_filters._record(os.getpid())
    assert controller_run_state(json.dumps({**record, "start": "an earlier start"}))["state"] == "gone"


@pytest.mark.parametrize("key", ["host", "boot"])
def test_a_run_of_another_machine_or_of_an_earlier_start_of_this_one_is_elsewhere(key):
    # Neither can be asked about here, so neither is ever taken for gone.
    record = {**desktop_filters._record(os.getpid()), key: "another-machine.example"}
    assert controller_run_state(json.dumps(record)) == {
        "state": "elsewhere",
        "host": record["host"],
        "pid": os.getpid(),
    }


@pytest.mark.parametrize(
    "recorded",
    [
        None,
        "",
        "not a record",
        "[1, 2]",
        "{}",
        json.dumps({"host": "here", "boot": "b", "pid": 12}),
        json.dumps({"host": "here", "boot": "b", "pid": 12, "start": ""}),
        json.dumps({"host": "here", "boot": "b", "pid": "12", "start": "s"}),
        json.dumps({"host": "here", "boot": "b", "pid": True, "start": "s"}),
        json.dumps({"host": "here", "boot": "b", "pid": -1, "start": "s"}),
        json.dumps({"host": "here", "boot": "b", "pid": 2**31, "start": "s"}),
        json.dumps({"host": "here", "boot": None, "pid": 12, "start": "s"}),
        json.dumps({"host": "", "boot": "b", "pid": 12, "start": "s"}),
    ],
)
def test_a_record_that_says_nothing_checkable_is_unknown(recorded):
    assert controller_run_state(recorded)["state"] == "unknown"


def test_only_a_host_name_and_a_pid_are_said_of_what_the_vm_handed_back():
    # A message quotes them: what the VM chose that is not a host name, or not a pid, is never said.
    record = {"host": "evil\x1b]0;retitled\x07", "boot": "b", "pid": "12; rm -rf ~", "start": "s"}
    assert controller_run_state(json.dumps(record)) == {"state": "unknown", "host": "", "pid": 0}


def test_a_start_this_machine_cannot_read_is_never_a_run_gone(monkeypatch):
    record = json.dumps(desktop_filters._record(os.getpid()))
    machine = desktop_filters._machine
    monkeypatch.setattr(desktop_filters, "_machine", lambda: (machine()[0], lambda pid: None))
    assert controller_run_state(record)["state"] == "unknown"
    monkeypatch.setattr(desktop_filters, "_machine", lambda: (None, lambda pid: None))
    assert controller_run_state(record)["state"] == "unknown"


def test_ps_reads_a_start_alike_under_any_zone_or_language(monkeypatch):
    # lstart is local time in the locale's words: two runs under another zone read one process's start as two.
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    here = desktop_filters._ps(os.getpid())
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    monkeypatch.setenv("LC_ALL", "C")
    assert here and desktop_filters._ps(os.getpid()) == here
    pid, _ = _ended()
    assert desktop_filters._ps(pid) == ""


@pytest.mark.skipif(not desktop_filters.BOOT_ID.exists(), reason="Linux's /proc only")
def test_linux_reads_a_start_from_proc_as_ticks_since_its_boot():
    # Which a clock set since leaves alone, where ps's lstart, worked out from it, moves with the clock.
    assert desktop_filters._proc(os.getpid()).isdigit()
    pid, _ = _ended()
    assert desktop_filters._proc(pid) == ""
