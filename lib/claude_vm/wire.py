"""The one way a record leaves the core: to_wire(), a JSON-shaped dict, format_version and kind first. Stdlib only."""

import dataclasses
import json
import re
from typing import Any

FORMAT_VERSION = "1.0"


def to_wire(record: Any) -> dict[str, Any]:
    """A record as JSON would carry it: tuples as lists, since Jinja's + cannot join a tuple to a list."""
    kind = re.sub(r"(?<!^)(?=[A-Z])", "_", type(record).__name__).lower()  # NextStep: next_step
    fields: dict[str, Any] = json.loads(json.dumps(record, default=_fields))
    return {"format_version": FORMAT_VERSION, "kind": kind} | fields


def _fields(record: object) -> dict[str, Any]:
    """json's hook, record by record: its fields, then the values its properties derive, which are never stored."""
    if not dataclasses.is_dataclass(record):
        raise TypeError(f"a {type(record).__name__} is no record")
    names = [field.name for field in dataclasses.fields(record)]
    names += [name for name, value in vars(type(record)).items() if isinstance(value, property)]
    return {name: getattr(record, name) for name in names}
