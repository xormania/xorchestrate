"""Read store-owned event order through the shared transaction port."""


def retained_events(transaction):
    after = 0
    while page := transaction.events(after=after, limit=1000):
        yield from page
        after = page[-1]["seq"]
