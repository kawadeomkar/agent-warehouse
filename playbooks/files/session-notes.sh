# The session notes' helper (playbooks/code.yml): fixed text, copied to the VM
# unchanged beside session_notes.py, and run as
# `bash -l <this file> <notes> <vm> <Remote Control unit> <time zone> [refresh-only]`
# by the editor's "Session notes" task when the window opens, beside the Claude
# task, and by the Claude terminal after a sign-in there.
#
# bash stays in charge whatever its Python part does - raises, hangs, or is
# missing - waits for it 5 seconds at most, opens the notes and exits 0, so the
# notes always open and the editor never prints the command line; set +e, since
# a login profile may set -e. session_notes.py, beside this file, rewrites the
# notes' live section from the VM. code -r opens the notes in this window,
# through the remote CLI VS Code Server puts on every task's PATH, in the
# foreground, so that the task's terminal does not close under it.
# refresh-only, from the Claude terminal after a sign-in there, waits for
# Remote Control to start, refreshes, and opens nothing: the preview follows
# the file. A login shell, because claude is on the login PATH only.
set +e
[ "$5" != refresh-only ] || sleep 5
python3 "${0%/*}/session_notes.py" "$1" "$2" "$3" "$4" >/dev/null 2>&1 &
notes_pid=$!
tries=0
while kill -0 "$notes_pid" 2>/dev/null && [ "$tries" -lt 50 ]; do
  sleep 0.1
  tries=$((tries + 1))
done
{ kill "$notes_pid"; wait "$notes_pid"; } 2>/dev/null
[ "$5" != refresh-only ] || exit 0
if [ -f "$1" ] && [ ! -L "$1" ] && command -v code >/dev/null 2>&1; then
  code -r "$1" </dev/null >/dev/null 2>&1
fi
exit 0
