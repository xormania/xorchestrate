"""Verify and safely restore this publication's content-addressed evidence."""
import argparse, hashlib, json, pathlib, zipfile
p=argparse.ArgumentParser();p.add_argument('archive',type=pathlib.Path);p.add_argument('--out',type=pathlib.Path);p.add_argument('--verify-only',action='store_true');a=p.parse_args()
if bool(a.out)==a.verify_only:p.error('select exactly one of --out or --verify-only')
summary=json.loads((a.archive.parent/'package-summary.json').read_text())
assert a.archive.stat().st_size==summary['archive_bytes']
assert hashlib.sha256(a.archive.read_bytes()).hexdigest()==summary['archive_sha256']
if a.out:a.out.mkdir(parents=True,exist_ok=False)
with zipfile.ZipFile(a.archive) as z:
 raw=z.read('MANIFEST.json');assert raw==(a.archive.parent/'package-manifest.json').read_bytes();m=json.loads(raw)
 names=[i.filename for i in z.infolist()];assert len(names)==len(set(names))
 assert set(names)=={'MANIFEST.json'}|{'objects/'+r['sha256'] for r in m['files']}
 restored=set();payloads={}
 for row in m['files']:
  relative=pathlib.PurePosixPath(row['path']);assert not relative.is_absolute() and '..' not in relative.parts and str(relative) not in restored
  restored.add(str(relative));h=row['sha256'];data=z.read('objects/'+h)
  assert hashlib.sha256(data).hexdigest()==h and len(data)==row['size']
  payloads[h]=True
  if a.out:
   dest=a.out/relative;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);dest.chmod(row['mode']&0o777)
   assert hashlib.sha256(dest.read_bytes()).hexdigest()==h
 assert len(restored)==m['file_count'] and len(payloads)==m['unique_payloads']
print(json.dumps({'verified':True,'files':len(restored),'unique_payloads':len(payloads),'restored':bool(a.out),'symlinks_recreated':False},indent=2))
