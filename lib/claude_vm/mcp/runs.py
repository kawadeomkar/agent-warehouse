"""A run tool's call following its job: each line the run prints sent as progress, which Claude Code shows the person
under the running call, and the call's result at the run's end - or earlier, the first time the run waits for the
person in the browser.

The sign-ins start minutes into a deploy, after Claude Code has moved the call into the background, where nothing the
person sees says that a page waits; only the agent can tell them. So the call returns at the run's browser block - the
callback's ACTION REQUIRED IN YOUR BROWSER, its own words, cleaned as the run printed them - with the job's ID and where
the block ended, and the job goes on: job(id, since) follows it to its next wait or its end, never returning a block
twice.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from claude_vm.mcp.jobs import Job

BROWSER = "ACTION REQUIRED IN YOUR BROWSER"
RULE = re.compile(r"^#{20,}$")
# How often the output is read, and how long a run may be silent before the call says it is still running: progress
# also keeps Claude Code's idle limit, 30 minutes for stdio by default, from ending the call.
POLL = 0.5
HEARTBEAT = 60
PROGRESS_CHARACTERS = 200
LOGGED = re.compile(r"^Logging this run to (\S+)$", re.MULTILINE)


@dataclass
class Followed:
    # running (the wait given ran out), waiting_for_you, or how the job ended: ok, failed, stopped, gone.
    state: str
    # Where in the output the next call goes on from.
    since: int
    # A browser block, for waiting_for_you.
    block: list[str]


def browser_block(lines: list[str]) -> tuple[int, int] | None:
    """Where the first browser block of lines starts and ends: its rule, its heading, its rule, and its body up to a
    blank line or the next rule."""
    for i, line in enumerate(lines):
        if not (line.startswith("##  ") and BROWSER in line):
            continue
        start = i - 1 if i and RULE.match(lines[i - 1]) else i
        end = i + 2 if i + 1 < len(lines) and RULE.match(lines[i + 1]) else i + 1
        while end < len(lines) and lines[end].strip() and not RULE.match(lines[end]) and end - i < 40:
            end += 1
        return start, end
    return None


async def follow(
    job: Job,
    since: int,
    wait_seconds: float | None,
    progress: Callable[[str], Awaitable[None]],
) -> Followed:
    """Follow job from byte since: each new line through progress, until it ends, waits for the person, or wait_seconds
    have passed."""
    began = last = time.monotonic()
    settled = False
    while True:
        text, offset = job.text(since)
        lines = text.splitlines()
        block = browser_block(lines)
        # A block at the very end may still be arriving: one more read first.
        if block and block[1] == len(lines) and not settled and job.state() == "running":
            settled = True
            await asyncio.sleep(POLL)
            continue
        if block:
            start, end = block
            for line in lines[:start]:
                await _said(progress, line)
            through = since + len("".join(f"{line}\n" for line in lines[:end]).encode())
            return Followed("waiting_for_you", through, lines[start:end])
        for line in lines:
            await _said(progress, line)
            last = time.monotonic()
        since = offset
        state = job.state()
        if state != "running":
            # What the run printed as it ended is read, said and searched for a block like the rest.
            if job.text(since)[0]:
                continue
            return Followed(state, since, [])
        now = time.monotonic()
        if wait_seconds is not None and now - began >= wait_seconds:
            return Followed("running", since, [])
        if now - last >= HEARTBEAT:
            await progress(f"still running, {int(now - began) // 60} min in")
            last = now
        settled = False
        await asyncio.sleep(POLL)


async def _said(progress: Callable[[str], Awaitable[None]], line: str) -> None:
    if line.strip():
        await progress(line.strip()[:PROGRESS_CHARACTERS])


def log_of(job: Job) -> str:
    """The run log's name, as tools/run-logged said it, under .logs/."""
    text, _ = job.text(0)
    found = LOGGED.search(text)
    return ".logs/" + found.group(1).rsplit("/", 1)[-1] if found else ""


def duration(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"
