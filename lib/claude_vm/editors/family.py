"""The seam every editor family meets: one function, a plan of the window from the row and the inputs."""

from __future__ import annotations

from typing import Protocol

from .model import Editor, OpenInputs, OpenPlan


class Family(Protocol):
    """A module of free functions, never a class; not @runtime_checkable: nothing checks it at run time, and the
    registry's annotation is the declaration a type checker reads."""

    def plan_open(self, editor: Editor, inputs: OpenInputs) -> OpenPlan: ...
