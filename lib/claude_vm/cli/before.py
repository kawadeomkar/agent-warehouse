"""The run's own words: what every handler receives beside its verb's values (docs/architecture/engine.md §16.3).

`RunWords` is one frozen record of what shapes a run of any verb rather than what the verb does: the system layer's
verbosity (-v) and the settings for one run (--set, as typed: D5 types them) from after the verb; --detach, which C13
acts on; --no-lock, the one word for a run that takes no lock, at the person's own risk, as `just RUN_LOCK= <recipe>`
is today (C6 acts on it); --attended=1, the agents' server's word that someone is at the screen with no terminal,
where ATTENDED=1 stands on a just line today; the hidden --detached-run, which C13's starter gives the run it starts;
and LOG_DIR and LOG_RETENTION_DAYS, read from the environment as today. Nothing else is read from the environment: an
exported RUN_LOCK or ATTENDED changes nothing.

Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# The fields of a verb's values that are the run's own, which move from the values into RunWords.
RUNS_OWN = ("verbosity", "set", "detach")
# What is read from the environment, as today's justfile reads it: where each run's text log goes (empty, none), and
# how many days logs are kept. As given; the runs layer (C12) types them.
ENVIRONMENT = ("LOG_DIR", "LOG_RETENTION_DAYS")


@dataclass(frozen=True)
class RunWords:
    """The run's own words. log_dir and log_retention_days are the environment's, None where it does not set them."""

    verbosity: int = 0
    set: tuple[str, ...] = ()
    detach: bool = False
    no_lock: bool = False
    attended: bool = False
    detached_run: bool = False
    log_dir: str | None = None
    log_retention_days: str | None = None


def run_words(
    before: Mapping[str, object], values: Mapping[str, object], environ: Mapping[str, str]
) -> tuple[RunWords, dict[str, object]]:
    """The run's own words, from the words before the verb, the run's own of the verb's values and the environment's
    two; and the verb's values without them, which go to its handler."""
    verbosity = values.get("verbosity", 0)
    settings = values.get("set", ())
    run = RunWords(
        verbosity=verbosity if isinstance(verbosity, int) else 0,
        set=tuple(str(word) for word in settings) if isinstance(settings, (list, tuple)) else (),
        detach=values.get("detach") is True,
        no_lock=before.get("no_lock") is True,
        attended=before.get("attended") == "1",
        detached_run=before.get("detached_run") is True,
        log_dir=environ.get("LOG_DIR"),
        log_retention_days=environ.get("LOG_RETENTION_DAYS"),
    )
    return run, {name: value for name, value in values.items() if name not in RUNS_OWN}
