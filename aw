#!/bin/sh
# aw, the new engine's command line, from this checkout (docs/architecture/engine.md §16.3): ./aw <verb> ..., or aw
# with a link to this file in a folder on PATH. Until 3.0 every command is still just's; aw help lists its own.
#
# It finds its checkout from its own path, every link to it followed and every folder by its physical path, and never
# from the working directory, so no justfile or checkout found upward ever runs in its place. It asks tools/env-run,
# from the checkout's own folder as every caller of it does, which Python environment the checkout runs - a virtualenv
# of your own (PYTHON, or python3 where it is one), else the checkout's own, which uv makes in the checkout's folder
# outside it - and runs tools/aw with that Python by its absolute path, isolated (-I), with the words as typed. Nothing
# of the folder you run it from decides which Python runs: not a version manager's choice there, nor a module there.
#
# install_env is install's one home: what just install does to the environment, from uv.lock, and git's pre-commit
# hook, as just init puts it. It runs in three cases and no other: by itself, once, where the checkout's own
# environment does not exist yet, which no run can be under way from (said on standard error, before the program);
# for aw install --lock, which writes uv.lock and needs no environment; and for --install-env, the one word of the
# launcher's own, which aw install's run passes it as a child that carries its job's marker (AGENT_WAREHOUSE_JOB). It
# never installs into a virtualenv of your own unasked: where one lacks the packages, the program names aw install.
# No Galaxy collection is installed (the engine needs none). Where aw on PATH is not this launcher, it says in one line
# how to put it there, and writes nothing outside the checkout.
#
# The program is told how a person runs it again, AGENT_WAREHOUSE_STARTED_AS, for every command line it prints: aw where
# aw on PATH is this launcher, else what was typed to start it (./aw). It is never handed a job's marker it inherited:
# AGENT_WAREHOUSE_JOB is unset before anything but --install-env, which reads it, so the program's own environment, as
# the system shows it, carries none, and nor does anything it starts.
set -eu

# The Python version the checkout's own environment runs on: the justfile's managed_python, which
# tests/unit/test_aw_cli_parse.py holds equal until just install calls this launcher.
managed_python=3.14

self=$0
case $self in */*) ;; *) self=./$self ;; esac
started=$self
while [ -L "$self" ]; do
  link=$(readlink -- "$self")
  case $link in /*) self=$link ;; *) self=$(dirname -- "$self")/$link ;; esac
done
# -P: a folder on the way that is a link is its target, as the kernel read the links above, so <link>/.. is the
# target's parent and never the folder the text names.
checkout=$(CDPATH='' cd -P -- "$(dirname -- "$self")" && pwd -P)
launcher=$checkout/${self##*/}
uv=${UV:-uv}

# A file's physical path, every link to it followed and its folder by its physical path, as the launcher's own is
# found above; empty where its folder cannot be entered.
physical() {
  file=$1
  while [ -L "$file" ]; do
    link=$(readlink -- "$file")
    case $link in /*) file=$link ;; *) file=$(dirname -- "$file")/$link ;; esac
  done
  dir=$(CDPATH='' cd -P -- "$(dirname -- "$file")" 2>/dev/null && pwd -P) || return 0
  printf '%s/%s\n' "$dir" "${file##*/}"
}

# How a person runs this launcher again: aw where aw on PATH is this launcher, else what was typed to start it.
started_as() {
  onpath=$(command -v aw 2>/dev/null) || onpath=
  case $onpath in
  /*) if [ "$(physical "$onpath")" = "$launcher" ]; then
    echo aw
    return
  fi ;;
  esac
  printf '%s\n' "$started"
}

# A word quoted for a POSIX shell, as the program quotes a line it prints.
quoted() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# Which environment the checkout runs, asked from the checkout's own folder - a version manager picks python3 by the
# folder it is run in, and python puts that folder first on sys.path - as tools/env-run --which says it there: $kind,
# own or managed, $named the Python it names, and $python that Python by its absolute path. A Python of your own is
# asked there which interpreter it is, so that the one run is the one tools/env-run found there, whatever the folder
# aw is run from: a bare name, a relative path (the checkout's, as for just) or a version manager's shim alike. Empty
# where it cannot be run.
pick() {
  which=$(cd "$checkout" && tools/env-run --which)
  kind=${which%% *}
  named=${which#* }
  python=$named
  if [ "$kind" = own ]; then
    python=$(cd "$checkout" && "$named" -I -c 'import sys; print(sys.executable)' 2>/dev/null) || python=
  fi
}

# install's one home, from the checkout's top: with --lock uv.lock written again after a pin is edited, installing
# nothing; else the locked packages into the environment tools/env-run finds - one of yours --inexact, with no
# download, after tools/stale_scripts.py names the packages whose commands start another Python; the checkout's own
# exactly the lock's, on a Python uv downloads - then git's pre-commit hook.
install_env() {
  cd "$checkout"
  command -v "$uv" >/dev/null 2>&1 || {
    echo "error: no $uv: install uv (https://docs.astral.sh/uv/getting-started/installation/), or name it with UV=" >&2
    exit 1
  }
  if [ "${1:-}" = --lock ]; then
    UV_PYTHON_DOWNLOADS=never "$uv" lock
    return
  fi
  pick
  case $kind in
  own)
    # Refused, with how to make one, for a Python that is no virtualenv's.
    tools/env-run --bin >/dev/null
    prefix=$("$python" -I -c 'import sys; print(sys.prefix)')
    reinstall=$("$python" -I tools/stale_scripts.py)
    # $reinstall is words: --reinstall-package <name>, for each.
    # shellcheck disable=SC2086
    UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT="$prefix" "$uv" sync --locked --inexact --python "$python" $reinstall
    [ -z "$reinstall" ] || "$python" -I tools/stale_scripts.py --check
    ;;
  *)
    venv=${python%/bin/python}
    echo "Installing into this checkout's own environment, $venv: PYTHON is unset and python3 is no virtualenv."
    UV_PYTHON_DOWNLOADS=automatic UV_PROJECT_ENVIRONMENT="$venv" "$uv" sync --locked --managed-python --python "$managed_python"
    ;;
  esac
  # The git hook names no environment (tools/git-hook), so each worktree commits through its own. Another tool's hook
  # moves aside to pre-commit.legacy, which the hook runs first; a core.hooksPath folder, which may be every
  # repository's, is left alone.
  if git config core.hooksPath >/dev/null; then
    echo "warning: git's core.hooksPath is set, so aw install put no git hook there: copy tools/git-hook into that folder as pre-commit to have one" >&2
  else
    hook=$(git rev-parse --git-path hooks/pre-commit)
    if ! cmp -s tools/git-hook "$hook" || ! [ -x "$hook" ]; then
      said="Installed git's pre-commit hook."
      if [ -e "$hook" ] || [ -L "$hook" ]; then
        if grep -q -e 'File generated by pre-commit' -e "agent-warehouse's pre-commit hook" -e "claude-on-proxmox's pre-commit hook" "$hook" 2>/dev/null; then
          rm -f "$hook"
          said="Updated git's pre-commit hook to this version's."
        else
          mv "$hook" "$hook.legacy"
          said="Installed git's pre-commit hook; another tool's, now pre-commit.legacy, still runs first."
        fi
      fi
      mkdir -p "$(dirname "$hook")"
      cp tools/git-hook "$hook"
      chmod 755 "$hook"
      echo "$said"
    fi
  fi
  # How to run aw from any folder, where aw on PATH is not this launcher: said, and nothing written outside the checkout.
  if [ "$(started_as)" != aw ]; then
    echo "To run aw from any folder, link it into a folder on your PATH: ln -s $(quoted "$launcher") ~/.local/bin/aw"
  fi
}

# aw install's own step, which its run starts as a child carrying its job's marker: a job's ID, sixteen hex digits.
if [ "${1:-}" = --install-env ]; then
  job=${AGENT_WAREHOUSE_JOB:-}
  case $job in
  '' | *[!0123456789abcdef]*) job= ;;
  esac
  if [ "${#job}" -ne 16 ] || [ "$#" -gt 2 ] || { [ "$#" -eq 2 ] && [ "$2" != --lock ]; }; then
    echo "error: --install-env is aw install's own step, which its run starts: run aw install" >&2
    exit 1
  fi
  shift
  (install_env "$@")
  exit 0
fi
# A run makes its own job and marks its own children: an inherited marker is another job's.
unset AGENT_WAREHOUSE_JOB
# uv.lock written again, which needs no environment.
if [ "$#" -eq 2 ] && [ "$1" = install ] && [ "$2" = --lock ]; then
  (install_env --lock)
  exit 0
fi

pick
case $kind in
managed)
  if ! [ -x "$python" ]; then
    echo "This checkout has no Python environment yet: aw makes it first, from uv.lock, as aw install does." >&2
    (install_env) >&2
    if ! [ -x "$python" ]; then
      echo "error: uv made no Python at $python: run aw install again, and read what it says" >&2
      exit 1
    fi
  fi
  ;;
*)
  # A Python that cannot be run is said here, never in the shell's words for a failed exec; one that runs but is no
  # virtualenv's is the program's to refuse, in its own sentence, at no second start.
  if [ -z "$python" ] || ! [ -f "$python" ] || ! [ -x "$python" ]; then
    echo "error: the Python $named cannot be run: set PYTHON to a virtualenv's python, or leave it unset and aw makes this checkout's own" >&2
    exit 1
  fi
  ;;
esac
AGENT_WAREHOUSE_STARTED_AS=$(started_as)
export AGENT_WAREHOUSE_STARTED_AS
exec "$python" -I "$checkout/tools/aw" "$@"
