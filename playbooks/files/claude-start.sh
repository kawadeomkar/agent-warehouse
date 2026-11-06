# The Claude terminal of a task the user starts in an editor's window (playbooks/code.yml): fixed text, copied to
# the VM unchanged into the helpers' directory of just code's own directory there, and run by just code's task, or
# by a task of the user's own, as `bash <this file>`, with no arguments, in the folder the terminal opens in. Any
# editor's: nothing in it is one editor's, and a task of the user's own names its path, so the path stays.
#
# Each run is a terminal of its own, so several can run side by side, and none starts a second claude on a
# conversation a claude already has open: two processes would write one transcript. Claude Code keeps a registry
# of its running sessions, ${CLAUDE_CONFIG_DIR:-~/.claude}/sessions/<pid>.json, each naming the folder it runs in
# (cwd) and how it was started (entrypoint: cli for a claude in a terminal; Remote Control's sessions say another).
# Where one of a terminal names this folder, at a pid that still runs claude, a fresh claude starts. Otherwise
# claude --continue picks the folder's latest conversation up; where there is none to continue, Claude Code 2.1
# says so and exits 1 at once, so a --continue that fails within its first seconds starts a fresh claude instead,
# and one that ran and then failed is not started again. A registry that is not there, or an entry that cannot be
# read or used (a cwd with a NUL in it, which no path holds), names no claude; a check that cannot be made at all
# starts a fresh claude, never a --continue.
#
# The registry is read by the VM's Python 3, stdlib only, isolated (-I): a program read from stdin otherwise has
# the current directory first on sys.path, and this one runs in the folder the terminal opens in, often a
# repository's root, whose json.py, subprocess.py or re.py would then run in place of the standard library's -
# before Claude Code asks whether the folder is trusted, and able to have the check ask for a --continue. -I also
# leaves out the PYTHON* variables and the user's site-packages. A pid counts as a running claude as the modules
# count it, playbooks/module_utils/claude_on_proxmox.py's four functions below copied unchanged (a test holds them
# equal): the native binary, or Claude Code's npm package under node, by the process's argv, from /proc or ps; one
# whose argv cannot be read counts as running.
#
# No exec, so the script ends with exit 0 whatever claude did.
if ! command -v claude >/dev/null 2>&1; then
  printf '%s\n' 'Claude Code is not installed on this VM yet: on your machine, just deploy <vm> --only configure.'
  exit 0
fi
python3 -I - "$PWD" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" >/dev/null 2>&1 <<'EOF'
import json
import os
import subprocess
import sys


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


def is_claude(argv):
    """Whether a process's argv is Claude Code's: the native binary, or the npm package under node."""
    names = [os.path.basename(arg) for arg in argv[:2]]
    return "claude" in names or any("@anthropic-ai/claude-code" in arg for arg in argv[:2])


def process_argv(pid, proc="/proc"):
    """The argv of process ``pid``, from /proc where there is one, else ps; None when it cannot be read."""
    if os.path.isdir(os.path.join(proc, "self")):
        try:
            with open(os.path.join(proc, str(pid), "cmdline"), "rb") as handle:
                return [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
        except FileNotFoundError:
            return []
        except OSError:
            return None
    try:
        found = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.split()


def running_claude(pid, proc="/proc"):
    """Whether ``pid`` is a running claude. A pid reused by another program since is not; one unreadable is."""
    if not alive(pid):
        return False
    argv = process_argv(pid, proc)
    return argv is None or is_claude(argv)


def _open_here(folder, config):
    """Whether a claude started in a terminal runs in ``folder``, by Claude Code's registry in ``config``."""
    try:
        names = os.listdir(os.path.join(config, "sessions"))
    except OSError:
        return False
    here = os.path.realpath(folder)
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(config, "sessions", name), encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        pid, cwd = entry.get("pid"), entry.get("cwd")
        if not (isinstance(pid, int) and pid > 0 and isinstance(cwd, str) and entry.get("entrypoint") == "cli"):
            continue
        # One entry that cannot be used is passed over, never the end of the whole check.
        try:
            if os.path.realpath(cwd) == here and running_claude(pid):
                return True
        except (OSError, ValueError):
            continue
    return False


# 0 only once the registry was read and names no claude here: anything else, a crash included, is a fresh claude.
sys.exit(3 if _open_here(sys.argv[1], sys.argv[2]) else 0)
EOF
checked=$?
if [ "$checked" -ne 0 ]; then
  claude
  exit 0
fi
started=$SECONDS
claude --continue
continued=$?
if [ "$continued" -ne 0 ] && [ $((SECONDS - started)) -lt 5 ]; then
  claude
fi
exit 0
