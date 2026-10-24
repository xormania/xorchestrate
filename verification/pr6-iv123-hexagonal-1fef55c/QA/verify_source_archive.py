"""Verify restored source tar bytes against the selected Git commit's blobs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--restored', type=Path, required=True)
p.add_argument('--candidate', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
metadata = json.loads((a.restored/'evidence/packaging/source-archive.json').read_text())
archive = a.restored/'evidence/packaging'/metadata['file']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == metadata['sha256']
identity = subprocess.check_output(['git', '-C', str(a.candidate), 'show', '-s', '--format=%H %T', 'HEAD'], text=True).strip()
assert identity == metadata['commit']+' '+metadata['tree']
tracked = subprocess.check_output(['git', '-C', str(a.candidate), 'ls-tree', '-rz', 'HEAD']).split(b'\0')
expected = {}
for row in tracked:
    if row:
        header, name = row.split(b'\t', 1)
        mode, kind, oid = header.split()
        expected[name.decode()] = {'mode': int(mode, 8), 'object': oid.decode()}
found = {}
with tarfile.open(archive) as stream:
    for member in stream:
        if member.isdir():
            continue
        assert member.name.startswith('candidate-source/')
        name = member.name[len('candidate-source/'):]
        data = member.linkname.encode() if member.issym() else stream.extractfile(member).read()
        oid = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        assert name in expected and oid == expected[name]['object'], name
        assert bool(member.issym()) == (expected[name]['mode'] == 0o120000), name
        # Git records link content/type, not symlink permission bits. A tar
        # writer may use 0777 for the link itself; executable modes apply to
        # regular source files only.
        if not member.issym():
            assert member.mode & 0o111 == expected[name]['mode'] & 0o111, name
        found[name] = {'git_blob': oid, 'sha256': hashlib.sha256(data).hexdigest()}
assert set(expected) == set(found)
assert not subprocess.check_output(['git', '-C', str(a.candidate), 'status', '--porcelain'], text=True)
result = {'verified': True, 'commit': metadata['commit'], 'tree': metadata['tree'],
          'source_archive_sha256': metadata['sha256'], 'files': len(found),
          'all_git_blob_ids_match': True, 'executable_and_symlink_modes_match': True,
          'candidate_clean': True}
a.out.write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
