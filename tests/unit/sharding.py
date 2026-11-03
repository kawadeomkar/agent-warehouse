"""How --shard splits the unit tests (tests/unit/conftest.py): each group of tests whole, where it costs least.

A group is a module whose tests share a module-scoped fixture, or a test on its own. Each shard runs on a CI runner of
its own with WORKERS xdist workers sharing two cores, and one worker runs a group from its first test to its last. So a
shard takes as long as its workers need for everything in it, or as its longest group, whichever is more, and each
group, the longest first, goes to the shard where that comes out least. The longest groups end up in shards of their
own: evening out summed time instead put other long modules beside test_github_auth.py and test_tailscale.py, whose
workers then slowed those two down, and their shards were the run's slowest.
"""

from __future__ import annotations

# xdist workers per shard: ci.yml's PYTEST_XDIST_AUTO_NUM_WORKERS, which test_shards.py holds to it.
WORKERS = 4


def split(weight: dict[str, float], count: int) -> dict[str, int]:
    """Each group's shard, 0 to count - 1, from how long each group takes."""
    total, longest, owner = [0.0] * count, [0.0] * count, {}
    for group in sorted(weight, key=lambda group: (-weight[group], group)):
        cost = [
            (max((total[shard] + weight[group]) / WORKERS, longest[shard], weight[group]), total[shard], shard)
            for shard in range(count)
        ]
        shard = min(cost)[2]
        total[shard] += weight[group]
        longest[shard] = max(longest[shard], weight[group])
        owner[group] = shard
    return owner
