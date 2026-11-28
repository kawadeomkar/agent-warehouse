"""A secret the new engine holds, which is never said (docs/architecture/engine.md §16.1).

A token's secret, a key, a password: wrapped in a `Secret` as soon as it is read, it is a mark wherever it is shown -
`repr`, `str`, an f-string, `%`, `format`, a log line, a traceback's locals - and it refuses to be pickled or turned
into JSON, so it cannot travel by accident to a file, a child or a message. Its plain value is read only by
`reveal()`, at the one place that sends it on: a request's header, a child's standard input. The modules that call
`reveal()` are named in tests/unit/test_aw_guards.py's `REVEALS`, each with why, and no other may. `struck(text)` puts
it as its mark wherever a text holds it, so that a peer's words that echo it can be said; `struck_all(text, secrets)`
does so for several, the longest first, so that of two where one holds the other the longer is struck whole, each in
every form a text quotes it in as well as its own (JSON's escape of it, with and without ASCII's, and Python's
`repr`'s, inside the quotes), since Ansible writes a mapping's diff through JSON and a failure's value through
`repr`; and `struck_end(text, secrets)` strikes an end of a text cut at a bound that is the beginning of one.

Standard library only, and nothing at import.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

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

    def struck(self, text: str) -> str:
        """text with every occurrence of the value put as MARK: for a peer's words, which may echo what it was sent,
        before any of them is said. It says nothing of the value but where it was; an empty value strikes nothing."""
        if not isinstance(text, str):
            raise TypeError(f"a Secret strikes itself from a str, not a {type(text).__name__}")
        return text.replace(self.__value, MARK) if self.__value else text

    @staticmethod
    def _longest_first(secrets: tuple[Secret, ...]) -> list[Secret]:
        """secrets in the order a text is struck of them: the longest value first, those of one length as given. It
        hands back the Secrets alone, never a value or its length."""
        return sorted(secrets, key=lambda secret: len(secret.__value), reverse=True)

    @staticmethod
    def _forms(secrets: tuple[Secret, ...]) -> list[str]:
        """Every form of secrets' values a text is struck of, the longest first, each once: for struck_all alone,
        which strikes them and hands back nothing of them."""
        found: dict[str, None] = {}
        for secret in secrets:
            value = secret.__value
            if not value:
                continue
            found[value] = None
            found[json.dumps(value)[1:-1]] = None
            found[json.dumps(value, ensure_ascii=False)[1:-1]] = None
            found[repr(value)[1:-1]] = None
        return sorted((form for form in found if form), key=len, reverse=True)

    @staticmethod
    def _begun(text: str, secrets: tuple[Secret, ...]) -> int:
        """How much of text's end is the beginning of one of secrets' values, the most of any: 0 where none is. It
        hands back a length of text's alone, never a value."""
        return max(
            (
                size
                for secret in secrets
                for size in range(min(len(secret.__value), len(text)), 0, -1)
                if text.endswith(secret.__value[:size])
            ),
            default=0,
        )

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


def struck_all(text: str, secrets: Iterable[Secret]) -> str:
    """text with every occurrence of each secret of secrets put as MARK, the longest first: of two where one holds the
    other, the shorter struck first would leave the rest of the longer, which is then no longer there to strike. It says
    nothing of a value but where it was."""
    held = tuple(secrets)
    if not all(isinstance(secret, Secret) for secret in held):
        raise TypeError("a text is struck of Secrets alone")
    if not isinstance(text, str):
        raise TypeError(f"a Secret strikes itself from a str, not a {type(text).__name__}")
    for form in Secret._forms(held):
        text = text.replace(form, MARK)
    return text


# What a decoder puts for bytes it cannot read: a cut inside a character leaves one at the end.
REPLACED = "�"


def struck_end(text: str, secrets: Iterable[Secret]) -> str:
    """text with its end put as MARK where that end is the beginning of one of secrets' values, the most of any, and
    the replacement characters after it, which a cut inside a character leaves: for words cut at a bound, which may stop
    partway through a secret. Called only where the text was cut, since an end that happens to begin a secret would say
    so; text as it is where no end does. It says nothing of a value but where it was."""
    held = tuple(secrets)
    if not all(isinstance(secret, Secret) for secret in held):
        raise TypeError("a text is struck of Secrets alone")
    if not isinstance(text, str):
        raise TypeError(f"a Secret strikes itself from a str, not a {type(text).__name__}")
    kept = text.rstrip(REPLACED)
    size = Secret._begun(kept, held)
    return kept[: len(kept) - size] + MARK if size else text
