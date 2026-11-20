"""Today's habits of the command line, which are no rule between options: ordinary code after the parse and the one
check, before anything is imported or sent (docs/architecture/engine.md §16.3).

- A VM's name with `_`, which Proxmox refuses in a name, is taken with `-` in its place, and said in today's words
  (the justfile's hyphenate_names); and `alpha,beta` is two names, as two words are.
- The two meanings of no names (docs/reference/commands.md, the words a recipe takes): with none, `deploy`, `deploy
  --only provision` and `destroy` act on the default VM, and the commands that read VMs or configure them again on
  every VM the fleet made. Resolved into one field, `default_vm`, given only where a verb that takes several names was
  given none: true for the default VM, false for every VM. A verb that takes one name (code, release) is given no
  default. The default VM's name is the handler's to read from the settings (the row vm_default_name), never this
  module's, which reads no file of the fleet.

H5 adds the question `deploy configure` asks at a terminal. Standard library only.
"""

from __future__ import annotations

from claude_vm.cli.parse import Refused
from claude_vm.schema.check import one_word
from claude_vm.schema.words import Verb

# The pattern of the words that name VMs, the ones these habits apply to.
VM_NAMES = "vm_name"
# Today's words for a name taken with a hyphen (the justfile's hyphenate_names).
HYPHENATED = "warning: Proxmox does not allow underscores in VM names, so {name} was changed to {changed}"


def _default_vm(verb: Verb, values: dict[str, object]) -> bool:
    """What no names means for verb: the default VM (True) or every VM the fleet made (False)."""
    if verb.name == "destroy":
        return True
    if verb.name == "deploy":
        # A whole deploy, and its stage that creates, act on the default VM; --check, configure and ssh-config on
        # every VM; the template takes no names.
        return not values.get("check") and values.get("only") in (None, "", "provision")
    return False


def names(verb: Verb, values: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """values with the VMs' names as the verb takes them, and default_vm where a verb that takes several was given none;
    and what to say of each name taken with a hyphen. Raises Refused where names that came to more than one were given
    to a verb that takes one."""
    words = verb.words
    if words is None or words.pattern != VM_NAMES:
        return values, []
    given = values.get(words.field)
    typed = [given] if isinstance(given, str) else list(given) if isinstance(given, list) else []
    split = [name for word in typed for name in word.split(",") if name]
    said = [HYPHENATED.format(name=name, changed=name.replace("_", "-")) for name in split if "_" in name]
    taken = [name.replace("_", "-") for name in split]
    out = {key: value for key, value in values.items() if key != words.field}
    if words.plural:
        if taken:
            out[words.field] = taken
        else:
            out["default_vm"] = _default_vm(verb, values)
    elif len(taken) > 1:
        raise Refused(one_word(verb.name, words))
    elif taken:
        out[words.field] = taken[0]
    return out, said
