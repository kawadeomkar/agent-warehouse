"""The steps the sign-ins take on this machine: its desktop, and the run waiting here for a sign-in; the filters of
filter_plugins/desktop.py."""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
from pathlib import Path

# The openers tried when BROWSER names none, in this order: macOS's, then the
# freedesktop one.
OPENERS = ("open", "xdg-open")
# Commands that read the URL again as a command line of their own - a shell's
# -c, Windows' cmd.exe /c start or PowerShell's Start-Process - where a page's
# query string (a & or a ;, in a page the VM chose) would be a command run here.
# By name, without a directory or Windows' .exe.
INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh", "busybox", "cmd", "powershell", "pwsh"}
)


def _through_interpreter(argv):
    """Whether argv starts an interpreter before it reaches the page: a word ahead of the one that holds %s (any word,
    when none does) named as one of INTERPRETERS. Every word counts, not only the first, so a launcher before one -
    env and its options, nohup, sudo -u - cannot hide it, and each of a word's own words, which env -S splits."""
    ahead = []
    for word in argv:
        if "%s" in word:
            break
        ahead += str(word).split()
    return any(_name(word) in INTERPRETERS for word in ahead)


def _name(word):
    name = os.path.basename(str(word).replace("\\", "/")).lower()
    return name[: -len(".exe")] if name.endswith(".exe") else name


def page_opener(url, browser, found):
    """Return the argv that opens ``url`` in a browser on this machine, or ``[]`` when nothing can.

    ``browser`` is the ``BROWSER`` environment variable, which is a list of
    commands separated by ``:``, each of which may hold ``%s`` where the URL
    goes; a command without one gets the URL as its last argument. Each command
    is split the way a shell would split it, so a quoted path with a space stays
    one argument. Python's ``webbrowser`` module tries the commands in turn
    until one starts; an argv holds only one, so only the first command (the
    first entry that is not empty) is taken, and the others are never tried.

    ``found`` is what ``which`` printed for the openers asked about, as paths or
    names: without a usable ``BROWSER``, ``open`` is used if it was found, then
    ``xdg-open``.

    A ``BROWSER`` command that is a shell or a Windows command interpreter
    (INTERPRETERS) opens nothing: it would read the URL as a command line of
    its own. The caller then prints the page for the person to open, as it does
    when this machine has no opener at all.
    """
    url = str(url)
    for entry in str(browser or "").split(":"):
        if not entry.strip():
            continue
        try:
            argv = shlex.split(entry)
        except ValueError:
            # Unbalanced quotes: no command can be read out of it.
            argv = []
        if argv:
            if _through_interpreter(argv):
                return []
            if any("%s" in arg for arg in argv):
                return [arg.replace("%s", url) for arg in argv]
            return [*argv, url]
        break
    names = {os.path.basename(str(path).strip()) for path in found or []}
    for opener in OPENERS:
        if opener in names:
            return [opener, url]
    return []


# ------------------------------------------------- the run waiting for a sign-in
# A sign-in started on a VM outlives a run stopped while it waits - a Ctrl-C, a closed terminal - since no `always`
# runs then: the VM records the run that waits for it, and the next run of this machine asks this machine whether
# that run is still under way. The record is the VM's to hand back, so it is read as text that may say anything.

# Linux's id of this boot: one machine's, never another's, and a new one each time it starts.
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
# A host name as a record may hold it and a message may quote it.
HOST_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")
# ps's start of a process is local time in the locale's words: read in C and UTC, two runs under another zone or
# language read one process's start alike.
PS_ENV = {"LC_ALL": "C", "TZ": "UTC0"}


def _proc(pid):
    """A process's start in ticks since boot, from Linux's /proc: '' when there is none at pid, None when unreadable."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except FileNotFoundError:
        return ""
    except (OSError, ValueError):
        return None
    # After the command's name, which may hold spaces and parentheses itself: the 22nd field is the start.
    fields = stat.rpartition(")")[2].split()
    return fields[19] if len(fields) > 19 else None


def _ps(pid):
    """A process's start as ps says it in C and UTC: '' when there is none at pid, None when ps cannot say."""
    import subprocess

    try:
        done = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True,
            text=True,
            env={**os.environ, **PS_ENV},
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    said = " ".join(done.stdout.split())
    if done.returncode == 0 and said:
        return said
    # ps finds no process at that pid: it says nothing, and exits 1, on macOS and on Linux alike. Anything it says
    # on stderr - a pid it refuses - is no answer.
    return "" if done.returncode == 1 and not said and not done.stderr.strip() else None


def _machine():
    """This machine as it is now, and how to read a process's start on it: where Linux names its boot, that boot and
    /proc's ticks, which a clock set since leaves alone; elsewhere (macOS) the start of PID 1, and ps's, which keeps
    a process's start as it was recorded then."""
    try:
        return BOOT_ID.read_text().strip(), _proc
    except OSError:
        return _ps(1), _ps


def _record(pid):
    """The process at pid as a record names it: this machine's name, this start of it, the pid and that process's
    start; '' where one cannot be read, which no check takes for gone."""
    boot, started = _machine()
    return {"host": socket.gethostname(), "boot": boot or "", "pid": pid, "start": started(pid) or ""}


def controller_run(_=None):
    """This ansible-playbook run, as a VM records the run waiting for what it started there: {host, boot, pid, start}.

    The pid is the run's own process: the parent of the worker a task's templates run in - a fork, multiprocessing's,
    which ansible-core makes for each task - or this process where there is none. The input is not read.
    """
    import multiprocessing

    parent = multiprocessing.parent_process()
    return _record(parent.pid if parent is not None else os.getpid())


def controller_run_state(recorded):
    """Whether the run a VM recorded (controller_run's, as JSON text) is still under way: {state, host, pid}.

    state is ``running`` - this machine's, and its process still the one of that start; ``gone`` - this machine's,
    and no process of that start at its pid any more, none or another; ``elsewhere`` - another machine's, or this
    one's before it last started, which this machine cannot ask about; or ``unknown`` - nothing recorded, a record
    that does not parse, or a start that cannot be read, here now or by the run then. Only ``gone`` says that nothing
    waits for the sign-in. host and pid are the record's, for a message: '' and 0 where it holds none fit to quote.
    """
    try:
        run = json.loads(str(recorded or ""))
    except ValueError:
        run = None
    if not isinstance(run, dict):
        return {"state": "unknown", "host": "", "pid": 0}
    host, boot, pid, start = (run.get(key) for key in ("host", "boot", "pid", "start"))
    said = {
        "host": host if isinstance(host, str) and HOST_NAME.fullmatch(host) else "",
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid < 2**31 else 0,
    }
    if not (said["host"] and said["pid"] and isinstance(boot, str) and isinstance(start, str) and start):
        return {"state": "unknown", **said}
    here, started = _machine()
    if here is None:
        return {"state": "unknown", **said}
    if host != socket.gethostname() or boot != here:
        return {"state": "elsewhere", **said}
    now = started(pid)
    if now is None:
        return {"state": "unknown", **said}
    return {"state": "running" if now == start else "gone", **said}
