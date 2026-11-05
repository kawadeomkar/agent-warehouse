"""The base of every record the core builds: plain() copies a value out of Ansible's lazy containers and tagged strings,
and Checked holds a frozen dataclass's fields to their annotations whenever one is built. Stdlib only."""

import types
import typing
from collections.abc import Callable, Mapping
from typing import Any, Literal, get_args, get_origin, get_type_hints


def plain(value: object, where: str) -> Any:
    """A value of Ansible's, copied out: an exact str, int, bool or None, a dict, or a tuple for a list."""
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        return str(value)  # an exact str: a tagged string defines no __str__ of its own, so its tags go
    if isinstance(value, int):
        return int(value)
    if isinstance(value, Mapping):
        return {plain(k, where): plain(v, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(plain(v, f"{where}[{i}]") for i, v in enumerate(value))
    raise ValueError(f"{where} is a {type(value).__name__}, which no field takes")


class Checked:
    """A record whose fields hold their annotations, checked whenever one is built, then by its own validate()."""

    __slots__ = ()

    def __post_init__(self) -> None:
        cls = type(self)
        rules = _RULES.get(cls) or _RULES.setdefault(cls, tuple((n, _rule(h)) for n, h in get_type_hints(cls).items()))
        for name, holds in rules:
            if not holds(getattr(self, name)):
                raise ValueError(f"{cls.__name__}.{name} cannot be {getattr(self, name)!r}")
        self.validate()

    def validate(self) -> None:
        """A record's own rules: a hook, since super() fails in a slots dataclass on 3.12."""


_RULES: dict[type, tuple[tuple[str, Callable[[object], bool]], ...]] = {}  # each record's, worked out once


def _rule(hint: Any) -> Callable[[object], bool]:
    origin, args = get_origin(hint), get_args(hint)
    if origin is Literal:
        return lambda v: type(v) is str and v in args
    if origin in (typing.Union, types.UnionType):  # Union[...] and X | Y, one type from 3.14
        arms = tuple(map(_rule, args))
        return lambda v: any(arm(v) for arm in arms)
    if origin is tuple and args[-1:] == (Ellipsis,):
        item = _rule(args[0])
        return lambda v: type(v) is tuple and all(map(item, v))
    if origin is tuple:
        items = tuple(map(_rule, args))
        return lambda v: type(v) is tuple and len(v) == len(items) and all(r(x) for r, x in zip(items, v, strict=True))
    return (lambda v: v is None) if hint is type(None) else (lambda v: type(v) is hint)  # exact: True is no int
