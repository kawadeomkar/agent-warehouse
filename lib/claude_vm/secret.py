"""A secret the new engine holds, which is never said (docs/architecture/engine.md §16.1).

A token's secret, a key, a password: wrapped in a `Secret` as soon as it is read, it is a mark wherever it is shown -
`repr`, `str`, an f-string, `%`, `format`, a log line, a traceback's locals - and it refuses to be pickled or turned
into JSON, so it cannot travel by accident to a file, a child or a message. Its plain value is read only by
`reveal()`, at the one place that sends it on: a request's header, a child's standard input. The modules that call
`reveal()` are named in tests/unit/test_aw_guards.py's `REVEALS`, each with why, and no other may.

Standard library only, and nothing at import.
"""

from __future__ import annotations

# What a secret is shown as, wherever it is shown.
MARK = "********"


class Secret:
    """A plain string that is never said: shown as MARK, never pickled or turned into JSON; reveal() reads it."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"a Secret holds a str, not a {type(value).__name__}")
        self.__value = value

    def reveal(self) -> str:
        """The plain value, for the one place that sends it on."""
        return self.__value

    def __repr__(self) -> str:
        return f"Secret({MARK!r})"

    def __str__(self) -> str:
        return MARK

    def __format__(self, spec: str) -> str:
        # Whatever the format asked for: an f-string's width or conversion never reaches the value.
        return MARK

    def __reduce_ex__(self, protocol):
        raise TypeError("a Secret is never pickled")

    def __getstate__(self):
        raise TypeError("a Secret is never pickled")

    # One value that never changes: a copy is the same Secret, never one made through pickle's protocol, and a record
    # copied deep (dataclasses.asdict) keeps it a Secret.
    def __copy__(self) -> Secret:
        return self

    def __deepcopy__(self, memo) -> Secret:
        return self
