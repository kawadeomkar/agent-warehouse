"""ssh -G of a configuration file: what an ssh makes of a destination from that file alone, as a child of the run's
(runs/children.py, the ssh kind, in a group of its own) that is ended with its group past a limit. engine/aliases.py
validates the managed SSH config with it and reads the person's own (card G8; docs/architecture/engine.md §16.4).
-G reads and prints, and dials nothing. asyncio is imported where it is used.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from . import runner

if TYPE_CHECKING:
    from ..runs.job import Job

# The seconds an ssh -G is given: it reads files, and a Match exec of the person's own is all that could hold it.
LIMIT = 10.0


async def resolved(
    job: Job, program: str, config: Path | str, environ: Mapping[str, str], *destination: str, limit: float = LIMIT
) -> tuple[bool, str, str]:
    """program -G -F config, then destination's words, in environ: whether it answered 0, what it said and its standard
    error. Ended with its group past limit seconds, which is an answer of no."""
    import asyncio

    from ..runs import children

    argv = [program or runner.SSH_PROGRAM, "-G", "-F", str(config), *destination]
    pipe = asyncio.subprocess.PIPE
    try:
        process = await children.start(
            job, argv, children.GROUP, dict(environ), asyncio.subprocess.DEVNULL, pipe, pipe, children.SSH
        )
    except OSError as error:
        return False, "", f"{argv[0]}: {error.strerror or error}"
    try:
        out, err = await asyncio.wait_for(process.communicate(), limit)
    except TimeoutError:
        runner._kill(process)
        await process.wait()
        return False, "", f"{argv[0]} -G gave no answer within {limit:g} s"
    except BaseException:
        runner._kill(process)
        raise
    return process.returncode == 0, out.decode(errors="replace"), err.decode(errors="replace")
