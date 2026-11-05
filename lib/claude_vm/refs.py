"""How every record names a VM and a command: VmRef, Action and command_line(), and NextStep. Stdlib only."""

import re
import shlex
from dataclasses import dataclass

from .records import Checked

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # a VM name as just takes it, and its SSH alias
RECIPE = re.compile(r"[a-z][a-z0-9-]*")  # a recipe's name


@dataclass(frozen=True, slots=True, kw_only=True)
class VmRef(Checked):
    """A VM as the core names one: by its name, which whoever made a tagged VM chose, and by its VM ID."""

    name: str
    vmid: int

    def validate(self) -> None:
        if not NAME.fullmatch(self.name) or not 100 <= self.vmid <= 999_999_999:  # Proxmox's range of VM IDs
            raise ValueError(f"not a VM: {self.name!r} ({self.vmid})")


@dataclass(frozen=True, slots=True, kw_only=True)
class Action(Checked):
    """A just command as data: what a NEXT STEPS record or a status fix says to run."""

    recipe: str
    args: tuple[str, ...] = ()

    def validate(self) -> None:
        if not RECIPE.fullmatch(self.recipe) or not all(arg.isprintable() for arg in self.args):
            raise ValueError(f"not a just command: {self.recipe!r} {self.args!r}")

    @property
    def line(self) -> str:
        """On the wire beside recipe and args, and never stored."""
        return command_line(self)


def command_line(action: Action) -> str:
    """The one display form of a command, as a person would type it."""
    return shlex.join(("just", action.recipe, *action.args))


@dataclass(frozen=True, slots=True, kw_only=True)
class NextStep(Checked):
    """One NEXT STEPS record: what is left for one VM in one matter, None once nothing is."""

    vm: VmRef
    matter: str
    action: Action | None
