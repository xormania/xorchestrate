"""Concrete private-file subjects and synchronized publication probes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from scripts.testkit.adapters.managed_store import ManagedStore


def owned_store(root, plan=None):
    store = ManagedStore(ManagedStore.allocate(root))
    store.initialize(plan or {"suite": "all", "mode": "protocol-fixture"})
    store.claim_owner()
    return store


def concurrent_calls(function, count=2):
    barrier = Barrier(count)

    def call(index):
        barrier.wait(timeout=5)
        try:
            return function(index)
        except Exception as error:
            return error

    with ThreadPoolExecutor(max_workers=count) as workers:
        return list(workers.map(call, range(count)))
