"""Why a call was cancelled: the reason Claude Code gives in notifications/cancelled, which the SDK never hands a tool.

A person's stop ends the run, as Ctrl-C at a terminal would: Esc on a running call sends "AbortError: user-cancel", and
x in /tasks on a backgrounded one "AbortError: The operation was aborted." (verified on 2.1.309 to 2.1.319). Anything
else leaves the run going for job to follow, since nobody said stop: the wall clock's "SdkError: Request timed out", a
reason not on the list - a later Claude Code that words a stop otherwise errs on the safe side, and cancel still stops
it - and the session closing. An idle abort sends no cancellation at all.

The server reads its stdin through a pipe a thread feeds (tee_stdin): each line is passed on as it came, after any
cancellation in it is noted, so the reason is there when the SDK cancels the call.
"""

from __future__ import annotations

import json
import os
import threading

STOPS = frozenset({"AbortError: user-cancel", "AbortError: The operation was aborted."})
# The reason of each request cancelled, by its ID as the SDK gives a tool its request's (a string).
REASONS: dict[str, str] = {}


def note(line: bytes) -> None:
    """Keep the reason, when line is a notifications/cancelled message."""
    if b"notifications/cancelled" not in line:
        return
    try:
        message = json.loads(line)
    except ValueError:
        return
    params = message.get("params") if isinstance(message, dict) else None
    if message.get("method") == "notifications/cancelled" and isinstance(params, dict) and "requestId" in params:
        REASONS[str(params["requestId"])] = str(params.get("reason") or "")


def reason(request_id: str) -> str:
    """The reason the request was cancelled with, taken: "" when none was given."""
    return REASONS.pop(request_id, "")


def tee_stdin() -> None:
    """Put a pipe in fd 0's place, fed from the real stdin by a thread that notes each cancellation first."""
    real = os.dup(0)
    read, write = os.pipe()
    os.dup2(read, 0)
    os.close(read)

    def pump() -> None:
        with os.fdopen(real, "rb") as source:
            for line in source:
                note(line)
                view = memoryview(line)
                while view:
                    view = view[os.write(write, view) :]
        os.close(write)

    threading.Thread(target=pump, name="stdin", daemon=True).start()
