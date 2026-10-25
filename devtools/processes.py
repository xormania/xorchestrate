"""Linux process identities retained for recovery after a managed supervisor dies."""

import json
import os
import signal
from functools import cache
from pathlib import Path

from .profiles import write_json
from .store import identifier


@cache
def boot_id():
    if int(Path('/proc/self/stat').read_text().split(' ', 1)[0]) != os.getpid():
        raise RuntimeError("Managed process recovery requires /proc in this PID namespace")
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def status(pid):
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return ({"pid": int(pid), "start": fields[19], "session": int(fields[3]), "boot": boot_id()}, int(fields[1]))
    except (FileNotFoundError, ProcessLookupError):
        return None


def identity(pid):
    observed = status(pid)
    return observed[0] if observed else None


def family(records, *, sessions=()):
    """Capture descendants, including an owned session after its leader exits."""
    table = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal() and (observed := status(int(entry.name))):
            table[int(entry.name)] = observed
    found = {row['pid']: row for row in records if table.get(row['pid'], (None,))[0] == row}
    sessions = set(sessions) | {row['pid'] for row in found.values() if row['session'] == row['pid']}
    while True:
        extra = {pid: row for pid, (row, parent) in table.items()
                 if pid not in found and (parent in found or row['session'] in sessions)}
        if not extra:
            return list(found.values())
        found.update(extra)


def register(store, member, generation, pid):
    record = identity(pid)
    if record:
        path = store.directory / "processes" / identifier(member) / generation / f"{pid}.json"
        temporary = path.with_suffix('.tmp')
        write_json(temporary, record)
        temporary.replace(path)
    return record


def registered_alive(store, member, generation, pid):
    """Check the retained supervisor identity without scanning its process family.

    Registration follows the session transaction, so an absent record does not
    yet establish process death. A retained birth identity also fences PID reuse.
    """
    if not generation or not pid:
        return None
    path = store.directory / "processes" / identifier(member) / generation / f"{pid}.json"
    try:
        saved = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    return saved["pid"] == pid and identity(pid) == saved


def owned(store, member, generation):
    if not generation:
        return []
    result = {}
    directory = store.directory / "processes" / identifier(member) / generation
    for path in directory.glob("*.json"):
        saved = json.loads(path.read_text())
        current = identity(saved["pid"])
        if not current or any(current[key] != saved[key] for key in current):
            continue  # Never signal a reused PID or an unverifiable old group.
        result[current["pid"]] = current
    return family(result.values()) if result else []


def alive(records):
    return [row for row in records if identity(row["pid"]) == row]


def signal_owned(records, sig):
    for row in records:
        try:
            # Pin the process before checking its birth identity; PID reuse after
            # this check cannot redirect the signal to an unrelated process.
            fd = os.pidfd_open(row["pid"])
            try:
                if identity(row["pid"]) == row:
                    signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        except ProcessLookupError:
            pass
