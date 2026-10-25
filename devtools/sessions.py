"""Fence native clients to one incarnation of a stable role member."""

import fcntl
from contextlib import contextmanager

from .store import identifier


@contextmanager
def lock(store, member, *, exclusive=False):
    directory = store.directory / "session-locks"
    directory.mkdir(exist_ok=True, mode=0o700)
    with (directory / (identifier(member) + ".lock")).open("a") as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX | fcntl.LOCK_NB) if exclusive else fcntl.LOCK_SH)
        except BlockingIOError as error:
            raise ValueError("The previous session still has a call in flight; retry after it finishes.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Lease:
    def __init__(self, store, member, generation=None, *, operator=False):
        self.store, self.member, self.operator = store, member, operator
        current = store.member(member)
        self.generation = generation if generation is not None else current["generation"]
        # A fresh exported connection can resume an external seat after native exit.
        # It still captures the generation, so the next native start fences it out.
        self.external = generation is None and current["harness"] == "external" and current["desired"] == "stopped"

    def check(self):
        if self.operator:
            return
        current = self.store.member(self.member)
        if current["generation"] != self.generation or (
            self.generation is not None and current["desired"] != "running" and not self.external
        ):
            raise ValueError("This session has stopped or been replaced. Connect the current session.")

    @contextmanager
    def hold(self):
        if self.operator:
            yield
            return
        with lock(self.store, self.member):
            self.check()
            yield
