"""Fixed discovery-acquisition/1 adapter and shared acquisition coordinator.

Direct SHA-1 objects (loose and pack v2/v3 with index v2) are read beneath
pinned directory descriptors. No Git process, configuration interpreter,
worktree import, revision expression, network, or object-store write exists.
Only the five closed operations expose object identities to the coordinator.
"""
from __future__ import annotations

import errno
import hashlib
import io
import os
import re
import stat
import struct
import sys
import time
import zlib
from itertools import pairwise
from pathlib import Path

sys.dont_write_bytecode = True

from lane_read import (  # noqa: E402
    CONCEPT_INDEX,
    DISCOVERY_LAW,
    SOURCE_BYTES,
    _text,
    canonical_bytes,
    digest,
    source_reading,
    strict_json,
    valid_ref,
)

RECORD_RE = re.compile(r'(?:ops/conductor/handoff/HANDOFF-[A-Za-z0-9._-]+\.md|ops/conductor/AAR-[A-Za-z0-9._-]+\.md)')
HEX40_RE = re.compile(r'[0-9a-f]{40}')
DIR_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
FIXED_PATHS = frozenset(
    [p for p, _ in DISCOVERY_LAW] + ['composer.lock']
    + [p for row in CONCEPT_INDEX for field in ('home', 'contract', 'registry', 'entrypoint')
       if (p := row[field]) is not None and not p.endswith('/')]
)


class Unavailable(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def outcome(data=None, reason=None):
    return dict(state='ok' if reason is None else 'unavailable', reason=reason,
                data=data if reason is None else None)


def os_reason(error):
    if error.errno == errno.ENOENT:
        return 'missing'
    if error.errno in (errno.ELOOP, errno.ENOTDIR):
        return 'unsafe-path'
    return 'unreadable'


def inode_identity(info):
    return info.st_dev, info.st_ino


def signature(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class Anchors:
    """Retain ancestors too: detect replacement without reopening pathnames."""
    def __init__(self, declarations):
        self.declarations = declarations
        self.fds = {}
        self.errors = {}
        self.links = []
        self.owned = []
        self.identities = []
        for name, path in sorted(declarations.items()):
            try:
                fd = os.open('/', DIR_FLAGS)
                self.owned.append(fd)
                for component in path[1:].split('/'):
                    child = os.open(component, DIR_FLAGS, dir_fd=fd)
                    self.owned.append(child)
                    self.links.append((fd, component, inode_identity(os.fstat(child))))
                    fd = child
                info = os.fstat(fd)
                self.fds[name] = fd
                self.identities.append(dict(id=name, device=info.st_dev, inode=info.st_ino))
            except OSError as error:
                self.errors[name] = os_reason(error)
                self.identities.append(dict(id=name, device=-1, inode=-1))

    def verify(self):
        for parent, component, pinned in self.links:
            try:
                info = os.stat(component, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or inode_identity(info) != pinned:
                    raise Unavailable('changed-during-read')
            except OSError as error:
                raise Unavailable('changed-during-read') from error

    def close(self):
        for fd in reversed(self.owned):
            os.close(fd)
        self.owned.clear()


class Files:
    def __init__(self, anchors, budget):
        self.anchors = anchors
        self.budget = budget
        self.links = []
        self.handles = {}
        self.before = {}
        self.owned = []
        self.absent = []

    def open(self, anchor, path, directory=False):
        self.budget.check()
        cache_key = (anchor, path, directory)
        if cache_key in self.handles:
            return self.handles[cache_key]
        if anchor not in self.anchors.fds:
            raise Unavailable(self.anchors.errors.get(anchor, 'unsafe-path'))
        fd = self.anchors.fds[anchor]
        parts = path.split('/') if path else []
        for index, component in enumerate(parts):
            flags = DIR_FLAGS if index < len(parts) - 1 or directory else FILE_FLAGS
            try:
                child = os.open(component, flags, dir_fd=fd)
            except OSError as error:
                if error.errno == errno.ENOENT:
                    self.absent.append((fd, component))
                raise Unavailable(os_reason(error)) from error
            self.owned.append(child)
            info = os.fstat(child)
            self.links.append((fd, component, inode_identity(info), stat.S_IFMT(info.st_mode)))
            if flags == FILE_FLAGS and not stat.S_ISREG(info.st_mode):
                raise Unavailable('object-type')
            fd = child
        if not directory:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise Unavailable('object-type')
            self.before[fd] = signature(info)
        self.handles[cache_key] = fd
        return fd

    def read(self, anchor, path, limit=SOURCE_BYTES):
        fd = self.open(anchor, path)
        size = os.fstat(fd).st_size
        if size > limit:
            raise Unavailable('oversized')
        raw = self.range(fd, 0, size + 1)
        if len(raw) > limit:
            raise Unavailable('oversized')
        if len(raw) != size or signature(os.fstat(fd)) != self.before[fd]:
            raise Unavailable('changed-during-read')
        return raw

    def range(self, fd, offset, count):
        self.budget.check()
        self.budget.reserve(count)
        raw = os.pread(fd, count, offset)
        self.budget.bytes += len(raw)
        self.budget.check()
        return raw

    def names(self, anchor, path, limit):
        fd = self.open(anchor, path, True)
        result = []
        with os.scandir(fd) as entries:
            for entry in entries:
                self.budget.check()
                result.append(entry.name)
                if len(result) > limit:
                    raise Unavailable('budget-exceeded')
        return sorted(result)

    def verify(self):
        self.anchors.verify()
        for parent, component, pinned, kind in self.links:
            try:
                info = os.stat(component, dir_fd=parent, follow_symlinks=False)
                if inode_identity(info) != pinned or stat.S_IFMT(info.st_mode) != kind:
                    raise Unavailable('changed-during-read')
            except OSError as error:
                raise Unavailable('changed-during-read') from error
        for fd, before in self.before.items():
            if signature(os.fstat(fd)) != before:
                raise Unavailable('changed-during-read')
        for parent, component in self.absent:
            try:
                os.stat(component, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise Unavailable('changed-during-read') from error
            raise Unavailable('changed-during-read')

    def close(self):
        for fd in reversed(self.owned):
            os.close(fd)
        self.owned.clear()


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.deadline = self.started + 10
        self.operation_deadline = self.deadline
        self.operations = 0
        self.bytes = 0

    def check(self):
        now = time.monotonic()
        if now > self.deadline or self.operations > 512 or self.bytes > 16 * SOURCE_BYTES:
            raise Unavailable('budget-exceeded')
        if now > self.operation_deadline:
            raise Unavailable('timeout')

    def reserve(self, count):
        self.check()
        if self.bytes + count > 16 * SOURCE_BYTES:
            raise Unavailable('budget-exceeded')

    def begin(self):
        self.operations += 1
        self.operation_deadline = min(self.deadline, time.monotonic() + 2)
        self.check()


class GitObjects:
    """SHA-1 verification is mandatory even for packed or delta objects."""
    def __init__(self, config, files, budget):
        self.config = config
        self.repository = config['discovery']['repository']
        self.common = self.repository['common_gitdir']
        self.gitdir = self.repository['gitdir']
        self.files = files
        self.budget = budget
        self.cache = {}
        self.pack_cache = {}
        self.packs = None
        self.verified_commits = set()
        self.verified_blobs = set()
        self.allowed_entries = set()
        self.preflight_done = False

    def optional(self, anchor, path, limit=SOURCE_BYTES):
        try:
            return self.files.read(anchor, path, limit)
        except Unavailable as error:
            if error.reason == 'missing':
                return None
            raise

    def preflight(self):
        if self.preflight_done:
            return
        self.files.anchors.verify()
        for path in ('objects/info/alternates', 'objects/info/http-alternates', 'info/grafts'):
            if self.optional(self.common, path) is not None:
                raise Unavailable('unsafe-path')
        try:
            if self.files.names(self.common, 'refs/replace', 1):
                raise Unavailable('unsafe-path')
        except Unavailable as error:
            if error.reason != 'missing':
                raise
        # No config option is executed. Reject formats/redirectors this reader
        # cannot interpret; ordinary aliases/helpers remain inert data.
        for anchor, path in ((self.common, 'config'), (self.gitdir, 'config.worktree')):
            raw = self.optional(anchor, path, 65536)
            if raw is not None and re.search(rb'(?im)(?:^\s*\[\s*include|\b(?:promisor|partialclone|objectformat|refstorage)\s*=)', raw):
                raise Unavailable('unsafe-path')
        link = self.optional(self.gitdir, 'commondir', 4096)
        declared = self.config['discovery']['anchors']
        if link is not None:
            try:
                target = os.path.abspath(os.path.join(declared[self.gitdir], link.decode('ascii').strip()))
            except UnicodeError as error:
                raise Unavailable('malformed') from error
            if target != declared[self.common]:
                raise Unavailable('repository-mismatch')
        elif inode_identity(os.fstat(self.files.anchors.fds[self.gitdir])) != inode_identity(os.fstat(self.files.anchors.fds[self.common])):
            raise Unavailable('repository-mismatch')
        # Verify .git as data, never as a redirection to open.
        try:
            root_fd = self.files.anchors.fds['repo']
            info = os.stat('.git', dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise Unavailable('unsafe-path')
            if stat.S_ISDIR(info.st_mode):
                fd = self.files.open('repo', '.git', True)
                if inode_identity(os.fstat(fd)) != inode_identity(os.fstat(self.files.anchors.fds[self.gitdir])):
                    raise Unavailable('repository-mismatch')
            elif stat.S_ISREG(info.st_mode):
                raw = self.files.read('repo', '.git', 4096)
                target = raw.decode('ascii').strip()
                if not target.startswith('gitdir: '):
                    raise Unavailable('malformed')
                if os.path.abspath(os.path.join(declared['repo'], target[8:])) != declared[self.gitdir]:
                    raise Unavailable('repository-mismatch')
            else:
                raise Unavailable('object-type')
        except (OSError, UnicodeError, KeyError) as error:
            raise Unavailable(os_reason(error) if isinstance(error, OSError) else 'unsafe-path') from error

        self.preflight_done = True

    def _inflate(self, data, limit=SOURCE_BYTES + 128):
        inflater = zlib.decompressobj()
        try:
            raw = inflater.decompress(data, limit + 1)
        except zlib.error as error:
            raise Unavailable('malformed') from error
        if len(raw) > limit or inflater.unconsumed_tail:
            raise Unavailable('oversized')
        if not inflater.eof:
            raise Unavailable('malformed')
        self.budget.reserve(len(raw))
        self.budget.bytes += len(raw)
        return raw

    def object(self, oid, depth=0):
        if oid in self.cache:
            return self.cache[oid]
        if not HEX40_RE.fullmatch(oid) or depth > 32:
            raise Unavailable('malformed')
        self.budget.check()
        raw = self.optional(self.common, 'objects/' + oid[:2] + '/' + oid[2:], SOURCE_BYTES + 1024)
        if raw is not None:
            inflated = self._inflate(raw)
            header, separator, data = inflated.partition(b'\0')
            fields = header.split(b' ')
            if not separator or len(fields) != 2 or not fields[1].isdigit() or int(fields[1]) != len(data):
                raise Unavailable('malformed')
            kind = fields[0]
        else:
            kind, data = self.packed_object(oid, depth)
        if len(data) > SOURCE_BYTES:
            raise Unavailable('oversized')
        if kind not in (b'commit', b'tree', b'blob', b'tag'):
            raise Unavailable('object-type')
        actual = hashlib.sha1(kind + b' ' + str(len(data)).encode('ascii') + b'\0' + data, usedforsecurity=False).hexdigest()
        if actual != oid:
            raise Unavailable('malformed')
        self.cache[oid] = (kind, data)
        return kind, data

    def pack_indexes(self):
        if self.packs is not None:
            return self.packs
        try:
            names = self.files.names(self.common, 'objects/pack', 1024)
        except Unavailable as error:
            if error.reason == 'missing':
                names = []
            else:
                raise
        if any(n.endswith('.promisor') for n in names):
            raise Unavailable('unsafe-path')
        self.packs = []
        for name in names:
            if not re.fullmatch(r'pack-[0-9a-f]{40}\.idx', name):
                continue
            fd = self.files.open(self.common, 'objects/pack/' + name)
            header = self.files.range(fd, 0, 1032)
            if len(header) != 1032 or header[:8] != b'\xfftOc\x00\x00\x00\x02':
                raise Unavailable('malformed')
            fanout = struct.unpack('>256I', header[8:])
            if any(a > b for a, b in pairwise(fanout)):
                raise Unavailable('malformed')
            count = fanout[-1]
            if 1032 + count * 28 + 40 > os.fstat(fd).st_size:
                raise Unavailable('malformed')
            self.packs.append((name[:-4] + '.pack', fd, fanout))
        return self.packs

    def packed_object(self, oid, depth):
        needle = bytes.fromhex(oid)
        for name, fd, fanout in self.pack_indexes():
            low = fanout[needle[0] - 1] if needle[0] else 0
            high = fanout[needle[0]]
            while low < high:
                middle = (low + high) // 2
                found = self.files.range(fd, 1032 + middle * 20, 20)
                if found < needle:
                    low = middle + 1
                else:
                    high = middle
            if low >= fanout[-1] or self.files.range(fd, 1032 + low * 20, 20) != needle:
                continue
            raw = self.files.range(fd, 1032 + fanout[-1] * 24 + low * 4, 4)
            if len(raw) != 4:
                raise Unavailable('malformed')
            offset = int.from_bytes(raw, 'big')
            if offset & 0x80000000:
                raw = self.files.range(fd, 1032 + fanout[-1] * 28 + (offset & 0x7fffffff) * 8, 8)
                if len(raw) != 8:
                    raise Unavailable('malformed')
                offset = int.from_bytes(raw, 'big')
            pack_fd = self.files.open(self.common, 'objects/pack/' + name)
            header = self.files.range(pack_fd, 0, 12)
            if len(header) != 12 or header[:4] != b'PACK' or int.from_bytes(header[4:8], 'big') not in (2, 3):
                raise Unavailable('malformed')
            return self.pack_entry(pack_fd, offset, depth)
        raise Unavailable('missing')

    def pack_entry(self, fd, offset, depth):
        self.budget.check()
        if depth > 32 or offset < 12 or offset >= os.fstat(fd).st_size - 20:
            raise Unavailable('malformed')
        if (fd, offset) in self.pack_cache:
            return self.pack_cache[fd, offset]
        cursor = offset

        def byte():
            nonlocal cursor
            raw = self.files.range(fd, cursor, 1)
            if not raw:
                raise Unavailable('malformed')
            cursor += 1
            return raw[0]

        first = byte()
        kind_id, size, shift = (first >> 4) & 7, first & 15, 4
        more = first & 128
        while more:
            if shift > 63:
                raise Unavailable('malformed')
            part = byte()
            size |= (part & 127) << shift
            shift += 7
            more = part & 128
        if size > SOURCE_BYTES:
            raise Unavailable('oversized')
        base = None
        if kind_id == 6:
            part = byte()
            distance = part & 127
            while part & 128:
                if distance > offset:
                    raise Unavailable('malformed')
                part = byte()
                distance = ((distance + 1) << 7) | (part & 127)
            if distance == 0:
                raise Unavailable('malformed')
            base = self.pack_entry(fd, offset - distance, depth + 1)
        elif kind_id == 7:
            raw_oid = self.files.range(fd, cursor, 20)
            cursor += 20
            if len(raw_oid) != 20:
                raise Unavailable('malformed')
            base = self.object(raw_oid.hex(), depth + 1)
        elif kind_id not in (1, 2, 3, 4):
            raise Unavailable('object-type')
        inflater = zlib.decompressobj()
        data = bytearray()
        while not inflater.eof:
            chunk = self.files.range(fd, cursor, min(4096, os.fstat(fd).st_size - 20 - cursor))
            if not chunk:
                raise Unavailable('malformed')
            cursor += len(chunk)
            try:
                data.extend(inflater.decompress(chunk, size + 1 - len(data)))
            except zlib.error as error:
                raise Unavailable('malformed') from error
            if len(data) > size or inflater.unconsumed_tail:
                raise Unavailable('oversized')
        if len(data) != size:
            raise Unavailable('malformed')
        self.budget.reserve(len(data))
        self.budget.bytes += len(data)
        if base is None:
            result = ((b'', b'commit', b'tree', b'blob', b'tag')[kind_id], bytes(data))
        else:
            result = (base[0], self.delta(base[1], bytes(data)))
        self.pack_cache[fd, offset] = result
        return result

    def delta(self, base, delta):
        cursor = 0

        def integer():
            nonlocal cursor
            result, shift = 0, 0
            while cursor < len(delta) and shift <= 63:
                part = delta[cursor]
                cursor += 1
                result |= (part & 127) << shift
                if not part & 128:
                    return result
                shift += 7
            raise Unavailable('malformed')

        if integer() != len(base):
            raise Unavailable('malformed')
        size = integer()
        if size > SOURCE_BYTES:
            raise Unavailable('oversized')
        result = bytearray()
        while cursor < len(delta):
            self.budget.check()
            command = delta[cursor]
            cursor += 1
            if command & 128:
                start, count = 0, 0
                for bit in range(7):
                    if command & (1 << bit):
                        if cursor >= len(delta):
                            raise Unavailable('malformed')
                        if bit < 4:
                            start |= delta[cursor] << (bit * 8)
                        else:
                            count |= delta[cursor] << ((bit - 4) * 8)
                        cursor += 1
                count = count or 65536
                if start + count > len(base) or len(result) + count > size:
                    raise Unavailable('malformed')
                result.extend(base[start:start + count])
            elif command:
                if cursor + command > len(delta) or len(result) + command > size:
                    raise Unavailable('malformed')
                result.extend(delta[cursor:cursor + command])
                cursor += command
            else:
                raise Unavailable('malformed')
        if len(result) != size:
            raise Unavailable('malformed')
        self.budget.reserve(len(result))
        self.budget.bytes += len(result)
        return bytes(result)

    def commit(self, oid):
        kind, raw = self.object(oid)
        if kind != b'commit':
            raise Unavailable('object-type')
        line = raw.partition(b'\n')[0]
        if not re.fullmatch(rb'tree [0-9a-f]{40}', line):
            raise Unavailable('malformed')
        self.verified_commits.add(oid)
        return line[5:].decode('ascii')

    def packed_refs(self, *, prefix=None, target=None):
        raw = self.optional(self.common, 'packed-refs')
        rows = {}
        if raw is None:
            return rows
        for raw_line in io.BytesIO(raw):
            line = raw_line.rstrip(b'\n')
            self.budget.check()
            if not line or line.startswith((b'#', b'^')):
                continue
            fields = line.split(b' ')
            if len(fields) != 2 or not re.fullmatch(rb'[0-9a-f]{40}', fields[0]):
                raise Unavailable('malformed')
            try:
                ref = fields[1].decode('ascii')
            except UnicodeError as error:
                raise Unavailable('malformed') from error
            # Other namespaces are never dereferenced. Replace storage is not
            # eligible even though direct reads would ignore its semantics.
            if ref.startswith('refs/replace/'):
                raise Unavailable('unsafe-path')
            if ref.startswith('refs/heads/'):
                if not valid_ref(ref) or ref in rows:
                    raise Unavailable('malformed')
                if (prefix is None or ref.startswith(prefix)) and (target is None or ref == target):
                    rows[ref] = fields[0].decode('ascii')
                    if prefix is not None and len(rows) > 64:
                        raise Unavailable('ref-overflow')
        return rows

    def ref_value(self, ref):
        if not valid_ref(ref):
            raise Unavailable('malformed')
        raw = self.optional(self.common, ref, 4096)
        if raw is None:
            oid = self.packed_refs(target=ref).get(ref)
            if oid is None:
                raise Unavailable('missing')
            return oid
        if not re.fullmatch(rb'[0-9a-f]{40}\n?', raw):
            raise Unavailable('object-type' if raw.startswith(b'ref:') else 'malformed')
        return raw.strip().decode('ascii')

    def anchor_head(self, head, ref):
        self.preflight()
        if head != self.repository['head'] or ref != self.repository['ref']:
            raise Unavailable('repository-mismatch')
        self.commit(head)
        # Observe HEAD, but the launch supplies the snapshot (including a
        # detached snapshot of a commit in a repository with a symbolic HEAD).
        raw_head = self.files.read(self.gitdir, 'HEAD', 4096)
        if not (re.fullmatch(rb'[0-9a-f]{40}\n?', raw_head) or
                (raw_head.startswith(b'ref: ') and valid_ref(raw_head[5:].strip().decode('ascii')))):
            raise Unavailable('malformed')
        ref_commit = self.ref_value(ref) if ref is not None else None
        if ref is not None and ref_commit != head:
            raise Unavailable('repository-mismatch')
        return dict(head=head, ref=ref, ref_commit=ref_commit)

    def list_refs(self, prefix):
        if prefix != 'refs/heads/records/' or prefix != self.config['discovery']['retained_refs']:
            raise Unavailable('unsafe-path')
        self.preflight()
        rows = self.packed_refs(prefix=prefix)
        if len(rows) > 64:
            raise Unavailable('ref-overflow')
        directories = [prefix[:-1]]
        examined = 0
        while directories:
            directory = directories.pop()
            try:
                fd = self.files.open(self.common, directory, True)
            except Unavailable as error:
                if error.reason == 'missing' and directory == prefix[:-1]:
                    continue
                raise
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.budget.check()
                    examined += 1
                    if examined > 4096:
                        raise Unavailable('ref-overflow')
                    ref = directory + '/' + entry.name
                    if not valid_ref(ref):
                        raise Unavailable('malformed')
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        directories.append(ref)
                    elif stat.S_ISREG(info.st_mode):
                        rows[ref] = self.ref_value(ref)
                        if len(rows) > 64:
                            raise Unavailable('ref-overflow')
                    else:
                        raise Unavailable('unsafe-path')
        result = []
        for ref, commit in sorted(rows.items()):
            self.commit(commit)
            result.append(dict(ref=ref, commit=commit))
        return result

    def tree(self, oid):
        kind, raw = self.object(oid)
        if kind != b'tree':
            raise Unavailable('object-type')
        cursor, names = 0, set()
        while cursor < len(raw):
            self.budget.check()
            end = raw.find(b'\0', cursor)
            if end < 0 or end + 21 > len(raw):
                raise Unavailable('malformed')
            fields = raw[cursor:end].split(b' ', 1)
            if len(fields) != 2:
                raise Unavailable('malformed')
            mode, name = fields
            if not name or b'/' in name or name in (b'.', b'..'):
                raise Unavailable('malformed')
            if name in names:
                raise Unavailable('malformed')
            names.add(name)
            yield name, mode.decode('ascii'), raw[end + 1:end + 21].hex()
            cursor = end + 21

    def lookup(self, commit, path):
        if commit not in self.verified_commits:
            raise Unavailable('unsafe-path')
        tree = self.commit(commit)
        for index, part in enumerate(path.split('/')):
            entry = next((e for e in self.tree(tree) if e[0] == part.encode('ascii')), None)
            if entry is None:
                return None
            _name, mode, oid = entry
            if index < len(path.split('/')) - 1:
                if mode != '40000':
                    raise Unavailable('object-type')
                tree = oid
            else:
                return dict(path=path, mode=mode, blob=oid)
        return None

    def tree_entry(self, commit, path):
        if path not in FIXED_PATHS and (commit, path) not in self.allowed_entries:
            raise Unavailable('unsafe-path')
        entry = self.lookup(commit, path)
        if entry is not None:
            if entry['mode'] not in ('100644', '100755'):
                raise Unavailable('object-type')
            self.verified_blobs.add(entry['blob'])
        return entry

    def list_tree(self, commit):
        if commit not in self.verified_commits:
            raise Unavailable('unsafe-path')
        result, examined, output_bytes = [], 0, 0
        for directory in ('ops/conductor', 'ops/conductor/handoff'):
            entry = self.lookup(commit, directory)
            if entry is None:
                continue
            if entry['mode'] != '40000':
                raise Unavailable('object-type')
            for name, mode, blob in self.tree(entry['blob']):
                examined += 1
                if examined > 4096:
                    raise Unavailable('tree-overflow')
                # Enumeration bytes include nonmatching entries too.
                output_bytes += len(name) + len(mode) + 42
                if output_bytes > SOURCE_BYTES:
                    raise Unavailable('oversized')
                try:
                    path = directory + '/' + name.decode('ascii')
                except UnicodeError:
                    continue
                if RECORD_RE.fullmatch(path):
                    if mode not in ('100644', '100755'):
                        raise Unavailable('object-type')
                    self.allowed_entries.add((commit, path))
                    self.verified_blobs.add(blob)
                    result.append(dict(path=path, mode=mode, blob=blob))
                    if len(result) > 256:
                        raise Unavailable('record-overflow')
        return sorted(result, key=lambda e: e['path'])

    def read_blob(self, blob):
        if blob not in self.verified_blobs:
            raise Unavailable('unsafe-path')
        kind, raw = self.object(blob)
        if kind != b'blob':
            raise Unavailable('object-type')
        return raw

    def operation(self, name, arguments):
        try:
            self.budget.begin()
            method = {'anchor_head': self.anchor_head, 'list_refs': self.list_refs,
                      'list_tree': self.list_tree, 'tree_entry': self.tree_entry,
                      'read_blob': self.read_blob}.get(name)
            if method is None:
                raise Unavailable('unsafe-path')
            result = method(**arguments)
            self.budget.check()
            return outcome(result)
        except Unavailable as error:
            return outcome(reason=error.reason)
        except OSError as error:
            return outcome(reason=os_reason(error))
        except (UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return outcome(reason='malformed')


class Acquisition:
    """Boot definitions and anchor identities persist; source readings do not."""
    def __init__(self, config, *, contract_bytes=None, implementation_bytes=None):
        self.config = config
        self.anchors = Anchors(config['discovery']['anchors'])
        tools_home = Path(__file__).absolute().parent
        if contract_bytes is None:
            contract_bytes = (tools_home.parent / 'discovery-lever.md').read_bytes()
        if implementation_bytes is None:
            implementation_bytes = {f'ops/conductor/tools/{name}': (tools_home / name).read_bytes()
                                    for name in ('lane_read.py', 'lane_mcp.py', 'lane_refs.py',
                                                 'mcp_transport.py', 'dispatch_observation.py')}
        self.definitions = {
            'config': config,
            'contract': dict(protocol='discovery/3', revision=hashlib.sha256(contract_bytes).hexdigest()),
            'adapter': dict(protocol='discovery-acquisition/1', git=None,
                            implementation=digest([dict(path=path, revision=hashlib.sha256(raw).hexdigest())
                                                   for path, raw in sorted(implementation_bytes.items())])),
        }
        self.binding = dict(configuration=config, anchors=self.anchors.identities)

    def close(self):
        self.anchors.close()

    def acquire(self, profile_id):
        config = self.config
        profile = config['profiles'][profile_id]
        budget = Budget()
        files = Files(self.anchors, budget)
        adapter = GitObjects(config, files, budget)
        observations = {ident: [dict(operation='definition', input={'id': ident},
                                    state='ok', reason=None, data=data)]
                        for ident, data in self.definitions.items()}
        observations.update(repo=[], retained=[])
        acquired = dict(state='ok', reason=None, sources=[], files={}, records=[],
                        records_complete=True, dispatches={}, head=None)
        cache = {}
        fatal = None

        def observe(source, operation, arguments, *, repeat=False):
            nonlocal fatal
            key = canonical_bytes([operation, arguments])
            if repeat or key not in cache:
                result = adapter.operation(operation, arguments)
                if not repeat:
                    cache[key] = result
            else:
                result = cache[key]
            observations[source].append(dict(operation=operation, input=arguments, **result))
            if result['reason'] in ('changed-during-read', 'budget-exceeded'):
                fatal = result['reason']
                raise Unavailable(fatal)
            return result

        def scan(source, ref, commit):
            tree = observe(source, 'list_tree', {'commit': commit})
            if tree['state'] != 'ok':
                acquired['records_complete'] = False
                return
            for entry in tree['data']:
                if len(acquired['records']) >= 256:
                    observations[source].append(dict(operation='list_tree', input={'commit': commit},
                                                     **outcome(reason='record-overflow')))
                    acquired['records_complete'] = False
                    # Discard this entire source's records; no partial set may
                    # establish absence or selection.
                    raise Unavailable('record-overflow')
                blob = observe(source, 'read_blob', {'blob': entry['blob']})
                if blob['state'] != 'ok':
                    acquired['records_complete'] = False
                acquired['records'].append(dict(kind='handoff' if '/handoff/' in entry['path'] else 'aar',
                                                data=blob['data'], reason=blob['reason'],
                                                provenance=dict(ref=ref, commit=commit, path=entry['path'], blob=entry['blob'])))

        try:
            repository = config['discovery']['repository']
            head_input = dict(head=repository['head'], ref=repository['ref'])
            head = observe('repo', 'anchor_head', head_input)
            acquired['head'] = head
            for path in sorted(FIXED_PATHS):
                if head['state'] != 'ok':
                    entry = outcome(reason=head['reason'])
                    observations['repo'].append(dict(operation='tree_entry', input=dict(commit=repository['head'], path=path), **entry))
                else:
                    entry = observe('repo', 'tree_entry', dict(commit=repository['head'], path=path))
                if entry['state'] == 'ok' and entry['data'] is None:
                    acquired['files'][path] = outcome(reason='missing')
                elif entry['state'] != 'ok':
                    acquired['files'][path] = entry
                else:
                    acquired['files'][path] = dict(observe('repo', 'read_blob', dict(blob=entry['data']['blob'])), mode=entry['data']['mode'])
            if head['state'] == 'ok':
                scan('repo', repository['ref'], repository['head'])
            else:
                acquired['records_complete'] = False
            refs_input = dict(prefix=config['discovery']['retained_refs'])
            refs = None
            if 'retained' in profile['source_ids']:
                refs = observe('retained', 'list_refs', refs_input)
                if refs['state'] != 'ok':
                    acquired['records_complete'] = False
                else:
                    for ref in refs['data']:
                        scan('retained', ref['ref'], ref['commit'])
            else:
                observations['retained'].append(dict(operation='disabled', input=None, **outcome(reason='disabled')))
            dispatch = config['discovery']['dispatch']
            for ident in config['job_ids']:
                source = 'dispatch/' + ident
                if source not in profile['source_ids']:
                    continue
                try:
                    budget.begin()
                    raw = files.read(dispatch['anchor'], dispatch['path'] + '/' + ident + '/record.json')
                    record = strict_json(raw)
                    if not isinstance(record, dict):
                        raise ValueError('record')
                    result = outcome(raw)
                    acquired['dispatches'][ident] = dict(recorded_status=_text(record.get('status')),
                                                         revision=hashlib.sha256(raw).hexdigest())
                except Unavailable as error:
                    result = outcome(reason=error.reason)
                    if error.reason == 'missing':
                        result['state'] = 'unknown'
                    if error.reason in ('changed-during-read', 'budget-exceeded'):
                        raise
                except OSError as error:
                    result = outcome(reason=os_reason(error))
                except (ValueError, UnicodeError, RecursionError):
                    result = outcome(reason='malformed')
                observations[source] = [dict(operation='read_record', input={'id': ident}, **result)]
            after = observe('repo', 'anchor_head', head_input, repeat=True)
            if head != after:
                raise Unavailable('changed-during-read')
            if refs is not None:
                after_refs = observe('retained', 'list_refs', refs_input, repeat=True)
                if refs != after_refs:
                    raise Unavailable('changed-during-read')
            files.verify()
            budget.check()
        except Unavailable as error:
            fatal = error.reason
        except OSError as error:
            fatal = os_reason(error)
        finally:
            files.close()
        budget.operation_deadline = budget.deadline
        acquired['sources'] = [source_reading(ident, self.binding, rows) for ident, rows in sorted(observations.items()) if rows]
        try:
            budget.check()
        except Unavailable as error:
            fatal = fatal or error.reason
        if fatal is not None:
            acquired.update(state='unavailable', reason=fatal)
        return acquired
