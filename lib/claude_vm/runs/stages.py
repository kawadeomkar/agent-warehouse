"""What a verb's run is made of, declared: its stages, in order, and each stage's steps, as a person reads them
(docs/architecture/engine.md §16.8).

A verb's workstream writes its own declaration, a `Declared(title, stages)`, and runs/model.py makes a run as a person
reads it from that declaration and the events the run emits; nothing there names a verb. Each `Stage` has a key, which
the run's `stage` and `step` events name (and a machine's outcome in the job's record keeps), the label a person reads,
its steps, whether its work is each machine's (its rows mark each machine's state; the template's and the golden
template's are the host's, and mark none), and whether it is quiet (counted and drawn only once something in it
failed). Each `Step` has a key and a label. A stage declares every step its events may name, or none.

This module writes `deploy`'s stages (DEPLOY) with today's labels, as lib/claude_vm/run/recipes.py declares them, and
the steps today's declaration names for them (the sign-ins, the editor's window); nothing else of `deploy`, whose
workstream adds the rest of its steps.

Pure data: the standard library alone, nothing read or written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A stage's or a step's key: lower-case letters and digits, in words joined by one hyphen. Held whole (fullmatch).
KEY = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def _key(key: object, what: str) -> str:
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError(f"{what}'s key is lower-case letters and digits in words joined by a hyphen, not {key!r}")
    return key


def _label(label: object, what: str) -> str:
    if not isinstance(label, str) or not label or label != label.strip() or not label.isprintable():
        raise ValueError(f"{what}'s label is one line of printable text, not {label!r}")
    return label


@dataclass(frozen=True)
class Step:
    """A step of a stage: its key, which a `step` event names, and the label a person reads."""

    key: str
    label: str

    def __post_init__(self) -> None:
        _key(self.key, "a step")
        _label(self.label, f"step {self.key}")


@dataclass(frozen=True)
class Stage:
    """A stage of a run: its key, which `stage` and `step` events name, the label a person reads, its steps in order
    (none: no step event names it), whether its work is each machine's (each_machine: its rows mark each machine's
    state; False where the work is the host's), and whether it is quiet (counted, and drawn only once something in it
    failed)."""

    key: str
    label: str
    steps: tuple[Step, ...] = ()
    each_machine: bool = True
    quiet: bool = False

    def __post_init__(self) -> None:
        _key(self.key, "a stage")
        _label(self.label, f"stage {self.key}")
        if not isinstance(self.steps, tuple) or not all(isinstance(step, Step) for step in self.steps):
            raise TypeError(f"stage {self.key}'s steps are a tuple of Step")
        keys = [step.key for step in self.steps]
        if len(set(keys)) != len(keys):
            raise ValueError(f"stage {self.key} declares a step twice")
        for name in ("each_machine", "quiet"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"stage {self.key}'s {name} is a bool")

    def step(self, key: str) -> Step | None:
        """The step of that key, or None where the stage declares none."""
        return next((step for step in self.steps if step.key == key), None)


@dataclass(frozen=True)
class Declared:
    """A verb's run, declared: its title (the verb) and its stages, in the order they run."""

    title: str
    stages: tuple[Stage, ...]

    def __post_init__(self) -> None:
        _label(self.title, "a declaration")
        if not isinstance(self.stages, tuple) or not self.stages:
            raise TypeError(f"{self.title}'s stages are a tuple of one Stage or more")
        if not all(isinstance(stage, Stage) for stage in self.stages):
            raise TypeError(f"{self.title}'s stages are a tuple of Stage")
        keys = [stage.key for stage in self.stages]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.title} declares a stage twice")

    def stage(self, key: str) -> Stage | None:
        """The stage of that key, or None where the declaration has none."""
        return next((stage for stage in self.stages if stage.key == key), None)


# deploy's stages, as today's declaration labels them (lib/claude_vm/run/recipes.py, RECIPES["deploy"]): the template
# and the golden template are the host's work, `finish` is quiet (today, the play that raises again what failed after
# the sign-ins), and the sign-ins' and the editor's window's steps are today's.
DEPLOY = Declared(
    "deploy",
    (
        Stage("template", "template", each_machine=False),
        Stage("provision", "provision"),
        Stage("configure", "configure"),
        Stage("aliases", "SSH aliases"),
        Stage(
            "sign-in",
            "sign in",
            steps=(Step("claude", "Claude"), Step("github", "GitHub"), Step("tailscale", "Tailscale")),
        ),
        Stage(
            "editor",
            "editor",
            steps=(
                Step("find", "find the VM"),
                Step("reach", "reach it"),
                Step("read-sessions", "read the sessions"),
                Step("folder", "the folder"),
                Step("alias", "alias"),
                Step("editor-here", "the editor here"),
                Step("directory", "the directory"),
                Step("copy-sessions", "copy the sessions"),
                Step("notes", "the notes"),
                Step("window", "window"),
            ),
        ),
        Stage("finish", "finish", quiet=True),
        Stage("golden", "golden template", each_machine=False),
    ),
)
