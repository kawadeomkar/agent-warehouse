# The Claude terminal of the window just code opens (playbooks/code.yml): fixed
# text, copied to the VM unchanged into the helpers' directory of just code's
# own directory there, and run by the editor's task as
# `bash -l <this file> <$1> ... <$13>`. Anything variable is an argument, never
# script text. The arguments, in order: what to say without claude; the line a
# sign-in here ends with where the claude_code role's path unit watches for the
# login, and so starts Remote Control, and the line where nothing does; that
# unit's file; GitHub's line, empty to leave GitHub alone; the git protocol and
# the scopes for gh; the VM's name; the session notes' helper, their path, the
# Remote Control unit and the time zone, the helper empty without notes -
# reached by shift; and --session, empty without one, read first as the last
# argument.
#
# A login shell, because tasks start non-login shells and claude is on the
# login PATH only. `claude auth status` exits non-zero until the VM is signed
# in, by claude.ai or by an API key; `claude auth login` is the browser flow,
# and the browser is the one on this machine, through the BROWSER helper VS
# Code Server sets in every terminal it starts. A sign-in that fails says how
# to try again, and stops. Then GitHub, when $5 says so (github_projects_auth,
# and not the token method) and gh is there but not signed in to github.com:
# asked of the active account alone, as the role asks, since gh otherwise
# checks every host and account and fails over a stale second one. gh's device
# flow runs with no terminal of its own so that it prints the code and the page
# at once and asks nothing - stdin closed, its output read line by line - with
# the page opened on this machine through that same BROWSER helper and the code
# printed on a line of its own to copy (a Remote-SSH terminal passes no
# clipboard escape through). The code is the seventh word of gh's line, read by
# word. Ctrl-C skips it: the trap keeps bash alive while gh and the read loop
# end, and claude starts as after a sign-in that failed. Then gh is made git's
# credential helper, so a push from this terminal works at once; the private
# repositories still take the just deploy that $5 names, and a sign-in that
# fails does not keep Claude from starting. pipefail, so that gh's exit status
# survives the read loop.
#
# After a sign-in that succeeded here, Claude's or gh's, the notes' helper runs
# once more in the background, refresh-only: it ran as the window opened and
# wrote "not signed in", and the preview follows the file. It ignores Ctrl-C,
# which in this terminal is meant for claude, and prints nothing here.
#
# With --session, claude --resume continues that one session, copied from this
# machine, under its own ID - never a fork, which every reload would start
# afresh from the copy - and nothing else: a resume that fails says why and
# starts no other claude. Otherwise --continue picks the folder's latest
# conversation up: a task terminal does not survive a window reload, after
# which the task runs again, so without it a reload would end the conversation
# (/clear starts a fresh one). Where there is none to continue - a VM nobody
# has talked to Claude on yet, whose only session may be Remote Control's -
# Claude Code 2.1 says "No conversation found to continue" and exits 1 at once,
# so a --continue that fails within its first seconds starts a fresh claude
# instead; one that ran and then failed is not started again. A copy of this
# machine's sessions never answers --continue: it is filed under the folder
# name it has here, not the checkout's.
#
# No exec, so the script ends with exit 0 whatever claude did, and a stop ends
# with exit 0 too: on a non-zero exit VS Code prints the whole command line,
# messages included, under the line that matters. The last argument is read
# with a for loop, which leaves its variable at the last word.
set -o pipefail
for resume; do :; done
if ! command -v claude >/dev/null 2>&1; then
  printf '%s\n' "$1"
  exit 0
fi
if ! claude auth status >/dev/null 2>&1; then
  printf '%s\n' "Signing in to Claude first. Your browser opens the sign-in page; if it shows a code, paste it here."
  if ! claude auth login; then
    printf '%s\n' 'Not signed in to Claude. To try again, reload the window or run "Tasks: Run Task", Claude.'
    exit 0
  fi
  if [ -e "$4" ]; then said=$2; else said=$3; fi
  [ -z "$said" ] || printf '%s\n' "$said"
  refresh=1
fi
if [ -n "$5" ] && command -v gh >/dev/null 2>&1 && ! gh auth status --hostname github.com --active >/dev/null 2>&1; then
  printf '%s %s\n' "Signing in to GitHub next, so that git push and gh work here; Ctrl-C skips this." \
    "Your browser opens github.com/login/device; enter the code below there."
  trap ':' INT
  if gh auth login -h github.com -p "$6" -w -s "$7" --skip-ssh-key </dev/null 2>&1 | while IFS= read -r line; do
      case "$line" in
        *"one-time code: "*)
          read -r _ _ _ _ _ _ code <<<"$line"
          printf '\n    %s\n\n' "$code"
          [ -z "$BROWSER" ] || "$BROWSER" https://github.com/login/device >/dev/null 2>&1 || true ;;
        *"Logged in as"*|*"failed to authenticate"*) printf '%s\n' "$line" ;;
      esac
    done && gh auth setup-git -h github.com; then
    printf '%s\n' "$5"
    refresh=1
  else
    printf '%s\n' "Not signed in to GitHub. From your machine: just login $8 --only github"
  fi
fi
trap - INT
if [ -n "$refresh" ] && [ -n "$9" ]; then
  vm=$8
  shift 8
  ( trap '' INT; bash "$1" "$2" "$vm" "$3" "$4" refresh-only ) </dev/null >/dev/null 2>&1 &
fi
if [ -n "$resume" ]; then
  claude --resume "$resume"
  exit 0
fi
started=$SECONDS
claude --continue
continued=$?
if [ "$continued" -ne 0 ] && [ $((SECONDS - started)) -lt 5 ]; then
  claude
fi
exit 0
