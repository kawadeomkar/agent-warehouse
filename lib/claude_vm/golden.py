"""A golden template's toolchain digest: the files of the baked roles an image is built from, and the options it is
built with, hashed. It knows no hypervisor: the provider adds its base's identity and its build number to make the key
(golden_key), so a second provider brings its own base and nothing here changes (docs/architecture/custom-code.md
§7.13).

The walk is the static guard's too (tests/unit/test_golden.py): every file a role's `image` entry point reaches, so the
digest changes with any of them, and with nothing else - a pull that touches only a role's sign-in files makes no new
golden. Stdlib and PyYAML only, as Ansible's own controller has."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Final

# The roles a golden template carries, each through its `image` entry point.
BAKED: Final = ("common", "dev_tools", "claude_code")
# The scrub the build runs on the image: in the digest, so a scrub made safer makes a new golden rather than leave the
# old ones selectable until they age out.
SCRUB: Final = ("playbooks/tasks/golden_generalize.yml",)

# A secret shorter than this is no secret worth the name: the example vault leaves values empty.
SECRET_FLOOR: Final = 4

_REPO: Final = Path(__file__).resolve().parents[2]
_INCLUDES: Final = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")
_SOURCES: Final = {"ansible.builtin.template": "templates", "ansible.builtin.copy": "files"}
_FILE_LOOKUP: Final = re.compile(r"""lookup\(\s*['"]ansible\.builtin\.file['"]\s*,\s*['"]([^'"{}]+)['"]\s*\)""")


def tasks(items: Iterable[dict] | None) -> Iterator[dict]:
    """Every task of a task list, those inside a block's block, rescue and always included."""
    for task in items or ():
        yield task
        for part in ("block", "rescue", "always"):
            yield from tasks(task.get(part))


def _included(task: dict) -> str | None:
    for key in _INCLUDES:
        if key in task:
            value = task[key]
            name = value if isinstance(value, str) else value.get("file", "")
            if "{{" in name:
                raise ValueError(f"{key} of a templated file name, {name!r}: the walk cannot follow it")
            return name
    return None


def reached(role: str, entry: str = "image", repo: Path = _REPO) -> list[Path]:
    """Every file of the role its entry point reaches: the task files it includes, by their literal names, the templates
    and files those name, the role's defaults and internals, and a module of its own library a task calls."""
    import yaml  # here, as a core module loads nothing heavy when it is imported (test_claude_vm.py)

    root = repo / "roles" / role
    seen: list[Path] = []
    queue = [root / "tasks" / f"{entry}.yml"]
    found: set[Path] = set()
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        if not path.is_file():
            raise ValueError(f"{path.relative_to(repo)}, which {role}'s {entry} entry point reaches, does not exist")
        seen.append(path)
        for task in tasks(yaml.safe_load(path.read_text())):
            name = _included(task)
            if name:
                queue.append(root / "tasks" / name)
            for module, folder in _SOURCES.items():
                args = task.get(module)
                if isinstance(args, dict) and isinstance(args.get("src"), str) and not args.get("remote_src"):
                    found.add(root / folder / args["src"])
            for name in _FILE_LOOKUP.findall(json.dumps(task, default=str)):
                found.add(root / "files" / name)
            for key in task:
                module = root / "library" / f"{key}.py"
                if module.is_file():
                    found.add(module)
    for each in ("defaults/main.yml", "vars/main.yml"):
        if (root / each).is_file():
            found.add(root / each)
    return sorted({*seen, *found})


def files(repo: Path = _REPO) -> list[Path]:
    """The files the digest hashes: every one a baked role's image entry point reaches, and the scrub."""
    scrub = {repo / each for each in SCRUB if (repo / each).is_file()}
    return sorted({path for role in BAKED for path in reached(role, repo=repo)} | scrub)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for each in (*value.keys(), *value.values()):
            yield from _strings(each)
    elif isinstance(value, (list, tuple)):
        for each in value:
            yield from _strings(each)


def secret_options(options: dict[str, dict[str, Any]], secrets: Iterable[Any]) -> list[str]:
    """The options, as role.option, whose value holds any of the secrets: a golden template is built with them, and
    what an install leaves on its disk is on every clone's, so no golden is made of them."""
    held = [s for s in _strings(list(secrets)) if len(s) >= SECRET_FLOOR]
    return [
        f"{role}.{name}"
        for role, values in sorted(options.items())
        for name, value in sorted(values.items())
        if any(secret in text for text in _strings(value) for secret in held)
    ]


def toolchain_digest(options: dict[str, dict[str, Any]], repo: Path = _REPO) -> str:
    """The digest of what an image is built from: every file files() names, and options, {role: {option: value}} for
    each baked role as its image_options entry point resolved them."""
    if sorted(options) != sorted(BAKED):
        raise ValueError(f"the options of {', '.join(BAKED)}, each, and no others; got {', '.join(sorted(options))}")
    digest = hashlib.sha256(b"toolchain/1\n")
    for path in files(repo):
        digest.update(
            f"{path.relative_to(repo).as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode()
        )
    digest.update(json.dumps(options, sort_keys=True, separators=(",", ":"), default=str).encode())
    return digest.hexdigest()


def golden_key(build: Any, base: str, digest: str) -> str:
    """A golden template's key: its build number, the identity of the base it is built from - the provider's to say -
    and the toolchain digest."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise ValueError(f"a toolchain digest is 64 hexadecimal digits, got {digest!r}")
    if not base:
        raise ValueError("a golden template's key needs its base's identity")
    return hashlib.sha256(f"golden/1\0{build}\0{base}\0{digest}".encode()).hexdigest()


def option_differences(options: dict[str, dict[str, Any]], shared: dict[str, dict[str, Any]]) -> list[str]:
    """The image options, as role.option, whose value in options - a VM's, as configure resolves them - differs from
    shared, those a golden was built with."""
    return [
        f"{role}.{name}"
        for role in BAKED
        for name in sorted(set((options or {}).get(role, {})) | set((shared or {}).get(role, {})))
        if json.dumps((options or {}).get(role, {}).get(name), sort_keys=True, default=str)
        != json.dumps((shared or {}).get(role, {}).get(name), sort_keys=True, default=str)
    ]
