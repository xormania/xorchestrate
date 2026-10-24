#!/usr/bin/env python3
"""Verify and restore the content-addressed independent review; no source execution."""
import argparse, hashlib, json, os, pathlib, stat, zipfile

def safe_name(name):
    p=pathlib.PurePosixPath(name)
    if p.is_absolute() or not name or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('unsafe archive path: '+name)
    return p

def digest_file(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=pathlib.Path)
    p.add_argument('--out',type=pathlib.Path)
    p.add_argument('--verify-only',action='store_true')
    a=p.parse_args()
    if not a.verify_only and a.out is None:p.error('--out is required to restore')
    out=a.out.resolve() if a.out else None
    with zipfile.ZipFile(a.archive) as z:
        if len(z.namelist())!=len(set(z.namelist())):raise ValueError('duplicate ZIP paths')
        for name in z.namelist():safe_name(name)
        m=json.loads(z.read('package-manifest.json'))
        assert m['schema']=='xorchestrate-independent-review-cas-v1'
        payloads={}
        for e in m['files']:
            safe_name(e['path']); safe_name(e['archive_path'])
            if e['archive_path'] not in payloads:
                data=z.read(e['archive_path'])
                if hashlib.sha256(data).hexdigest()!=e['sha256'] or len(data)!=e['bytes']:
                    raise ValueError('content identity mismatch: '+e['path'])
                payloads[e['archive_path']]=(e['sha256'],len(data))
            if payloads[e['archive_path']]!=(e['sha256'],e['bytes']):raise ValueError('inconsistent manifest')
            if a.verify_only:continue
            target=out.joinpath(*safe_name(e['path']).parts)
            for parent in target.parents:
                if parent==out.parent:break
                if parent.is_symlink():raise ValueError('symlink output parent')
            target.parent.mkdir(parents=True,exist_ok=True)
            if e['kind']=='symlink':
                link=z.read(e['archive_path']).decode()
                if os.path.isabs(link) or not (target.parent/link).resolve().is_relative_to(out):raise ValueError('escaping symlink')
                if target.is_symlink():
                    if os.readlink(target)!=link:raise ValueError('conflicting symlink')
                elif target.exists():raise ValueError('conflicting file')
                else:target.symlink_to(link)
            else:
                if target.is_symlink():raise ValueError('conflicting symlink')
                if target.exists():
                    if not target.is_file() or digest_file(target)!=e['sha256']:raise ValueError('conflicting existing output: '+str(target))
                else:
                    with target.open('xb') as f:f.write(z.read(e['archive_path']))
                os.chmod(target,e['mode'] & 0o777)
        result={'status':'passed','files_verified':len(m['files']),'unique_payloads_verified':len(payloads),'restored':not a.verify_only,'candidate':m['candidate']}
        print(json.dumps(result,indent=2))

if __name__=='__main__':main()
