"""What every adapter in filter_plugins/ shares: filters that import their core module only when one is called, and the
check that the claude_vm imported is the adapter's own checkout's. Stdlib only, and it imports no other core module.

An adapter puts its checkout's lib/ first on sys.path, relative to its own file, and returns filters(...) from its
FilterModule: every worker executes every file in filter_plugins/ at its first lookup of any filter, so nothing of the
core is imported there (docs/architecture/custom-code.md §7.10).
"""

import importlib
import os
from collections.abc import Callable, Iterable
from typing import Any

LIB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OWN: set[str] = set()


def own(lib: str) -> None:
    """Fail unless this claude_vm is the one under lib: the same directory, not the same string, since APFS ignores case
    and a resolved path keeps the case it was given."""
    if lib not in _OWN:
        if not os.path.samefile(LIB, lib):
            raise ImportError(f"claude_vm is {LIB}, not this checkout's, under {lib}")
        _OWN.add(lib)


def filters(lib: str, module: str, names: Iterable[str]) -> dict[str, Callable[..., Any]]:
    """Ansible's filters named, of claude_vm.<module>, each importing that module when it is first called."""

    def lazy(name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            own(lib)
            return getattr(importlib.import_module(f"claude_vm.{module}"), name)(*args, **kwargs)

        call.__name__ = call.__qualname__ = name
        return call

    return {name: lazy(name) for name in names}
