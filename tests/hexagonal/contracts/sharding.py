"""Independent expectations for partitioning a test selection across shards."""

VALID_SPECS = {"1/1": (1, 1), "1/4": (1, 4), "4/4": (4, 4), "3/7": (3, 7), "12/12": (12, 12)}
INVALID_SPECS = ("0/4", "5/4", "1/0", "0/0", "-1/4", "1", "1/", "/4", "a/b", "1/4/2", " 1/4", "1 /4",
                 "01/4", "1.0/4", "")


def complete_partition(selection, shards):
    """Shards are disjoint, each nonempty, and their union is exactly the selection."""
    selection = set(selection)
    seen = []
    for index, members in enumerate(shards, 1):
        members = list(members)
        assert members, f"shard {index}/{len(shards)} is empty"
        assert len(members) == len(set(members)), f"shard {index} repeats a module"
        seen.extend(members)
    assert len(seen) == len(set(seen)), "a module is assigned to more than one shard"
    assert set(seen) == selection, {"unassigned": sorted(selection - set(seen)),
                                    "unexpected": sorted(set(seen) - selection)}


def balanced(shards, weights, default):
    """Greedy balance bound: no shard exceeds the mean load by more than the heaviest module."""
    loads = [sum(weights.get(path, default) for path in members) for members in shards]
    heaviest = max(weights.get(path, default) for members in shards for path in members)
    assert max(loads) <= sum(loads) / len(loads) + heaviest + 1e-9, loads


def selected_cases(inventory):
    return {case["id"] for case in inventory["cases"] if case["selected"]}


def sharded_inventories(unsharded, shards, count):
    """Shard inventories partition the unsharded selected cases and identify their shard."""
    cases = [selected_cases(inventory) for inventory in shards]
    assert all(cases), "every shard selects cases"
    assert sum(len(item) for item in cases) == len(set().union(*cases)), "a case ran in more than one shard"
    assert set().union(*cases) == selected_cases(unsharded)
    assert unsharded["shard"] is None
    for index, inventory in enumerate(shards, 1):
        assert inventory["status"] == "passed"
        assert inventory["collected"] == unsharded["collected"], "each shard still validates full discovery"
        assert (inventory["shard"]["index"], inventory["shard"]["count"]) == (index, count)
        assert {case["path"] for case in inventory["cases"] if case["selected"]} == set(inventory["shard"]["modules"])
