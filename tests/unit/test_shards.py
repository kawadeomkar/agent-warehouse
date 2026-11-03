"""tests/unit/conftest.py's --shard, as CI splits the unit tests (docs/architecture/ci.md): N shards that between them
run every test exactly once, each module whose tests share a fixture whole in one of them, and the longest of those
in shards of their own (sharding.py).

Each look is a collection of the suite in a pytest of its own, as each CI shard collects it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sharding
import yaml

REPO = Path(__file__).resolve().parents[2]
PYTEST = Path(sys.executable).with_name("pytest")


def _collected(*args: str) -> list[str]:
    """The node IDs a run of tests/unit with args would run."""
    # Nothing of this run's own pytest, an xdist worker's included.
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTEST")}
    result = subprocess.run(
        [str(PYTEST), "-p", "no:cacheprovider", "-o", "addopts=", "--collect-only", "-q", "tests/unit", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


@pytest.fixture(scope="module")
def everything() -> list[str]:
    return _collected()


@pytest.mark.parametrize("count", [2, 3, 4])
def test_the_shards_run_every_test_exactly_once(everything, count) -> None:
    shards = [_collected("--shard", f"{index}/{count}") for index in range(1, count + 1)]
    assert all(shards), [len(shard) for shard in shards]
    ran = [test for shard in shards for test in shard]
    assert sorted(ran) == sorted(everything)


def test_a_module_whose_tests_share_a_fixture_is_never_split() -> None:
    shards = [_collected("--shard", f"{index}/3") for index in range(1, 4)]
    for module in ("test_github_auth.py", "test_tailscale.py", "test_sign_in_plays.py"):
        holding = [index for index, shard in enumerate(shards, 1) if any(f"/{module}::" in test for test in shard)]
        assert len(holding) == 1, (module, holding)


def test_the_longest_groups_get_shards_of_their_own() -> None:
    # Two groups far longer than any other, as test_github_auth.py and test_tailscale.py are, three long ones and many
    # short tests: the two have a shard each and nothing beside them, and the rest share the other two.
    weight = {"giant-a": 600, "giant-b": 590, "long-a": 380, "long-b": 300, "long-c": 300, "middle": 230}
    weight |= {f"short-{n}": 5 for n in range(200)}
    owner = sharding.split(weight, 4)
    alone = {owner["giant-a"], owner["giant-b"]}
    assert len(alone) == 2
    assert {group for group, shard in owner.items() if shard in alone} == {"giant-a", "giant-b"}
    assert set(owner.values()) == {0, 1, 2, 3}


def test_the_split_counts_the_workers_each_ci_shard_runs() -> None:
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    (step,) = [step for step in workflow["jobs"]["unit"]["steps"] if step.get("name") == "Unit tests"]
    assert step["env"]["PYTEST_XDIST_AUTO_NUM_WORKERS"] == str(sharding.WORKERS)
