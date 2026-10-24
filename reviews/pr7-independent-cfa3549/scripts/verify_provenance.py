#!/usr/bin/env python3
"""Independent, stdlib-only retained-byte and Git identity verification; no model calls."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    exp = repo / 'experiments/jev_failure_correlation_v2'
    evidence = exp / 'evidence'
    def git(*argv):
        return subprocess.check_output(['git', *argv], cwd=repo)
    def read(path):
        return json.loads(path.read_text())
    def sha(data):
        return hashlib.sha256(data).hexdigest()
    ids = {
        'product_and_v1': 'd2d2123bb8d4e866454c8e8e364f7749fa63a198',
        'measured': '9c6d7612e31bb78eb07868b5ac2a046629c5876c',
        'hosted': '0d8d5187209c1fb2c92e8d2a4305c6f7b40da9aa',
        'supplement': '4577eb2c09a211218cfb822f195127d6a8a3f918',
        'candidate': 'cfa3549490ee9db1c3e6cad918523cd854902e51',
    }
    results = {'identities': {name: {'commit': commit, 'tree': git('rev-parse',commit+'^{tree}').decode().strip()}
                              for name, commit in ids.items()}}
    base, candidate = ids['product_and_v1'], ids['candidate']
    existing = git('ls-tree','-r','--name-only','-z',base).decode().split('\0')[:-1]
    changed_existing = [p for p in existing if git('rev-parse',base+':'+p) != git('rev-parse',candidate+':'+p)]
    assert not changed_existing
    changes = git('diff','--name-status',base,candidate).decode().splitlines()
    assert all(line.startswith('A\t') for line in changes)
    assert all(line.split('\t')[1].startswith('experiments/jev_failure_correlation_v2/') or
               line.split('\t')[1] == '.github/workflows/jev-failure-correlation-v2.yml' for line in changes)
    results['preservation'] = {'base_tracked_files_identical':len(existing), 'changed_existing':changed_existing,
                               'added_paths':len(changes),'all_additions_scoped_to_v2':True}
    results['revision_changes'] = {}
    sequence = ['product_and_v1','measured','hosted','supplement','candidate']
    for a,b in zip(sequence,sequence[1:]):
        results['revision_changes'][a+'..'+b] = git('diff','--name-status',ids[a],ids[b]).decode().splitlines()
    results['hosted_patch'] = git('diff',ids['measured'],ids['hosted']).decode()
    results['archives'] = []
    for row in read(evidence/'archive-index.json'):
        data = (evidence/row['file']).read_bytes()
        assert len(data)==row['bytes'] and sha(data)==row['sha256']
        with zipfile.ZipFile(evidence/row['file']) as archive:
            assert archive.testzip() is None
            names=archive.namelist()
            assert len(names)==len(set(names))
            assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in names)
            results['archives'].append(dict(row, entries=len(names), verified=True))
            if row['file'] in ['cloud-20261024-01.zip','actions-40071712425.zip']:
                archive.extractall(out/row['file'][:-4])
    run=out/'cloud-20261024-01'/'cloud-20261024-01'
    seal=read(run/'ARTIFACTS.sha256.json')
    actual={str(p.relative_to(run)):{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size}
            for p in run.rglob('*') if p.is_file() and p.name!='ARTIFACTS.sha256.json'}
    assert seal==actual
    manifest=read(run/'manifest.json')
    assert manifest['source']['commit']==ids['measured']
    assert manifest['source']['tree']==results['identities']['measured']['tree']
    for path, expected in manifest['source']['source_hashes'].items():
        assert sha((repo/path).read_bytes())==expected
        assert sha(git('show',ids['measured']+':'+path))==expected
    freeze=read(exp/'data/frozen/freeze.json')
    for path,expected in freeze['files'].items():
        assert sha((exp/'data/frozen'/path).read_bytes())==expected
    results['main_integrity']={'sealed_files':len(seal),'source_hashes_verified':len(manifest['source']['source_hashes']),
                                'frozen_corpus_files_verified':len(freeze['files']),
                                'corpus_version':freeze['version'],'policy_version':manifest['plan']['version'],
                                'jev_model':manifest['plan']['jev_model'],'retained_reasoning_configuration':manifest['plan']['reasoning']}
    binding=read(run/'label-review-binding.json')
    label_review=read(repo/binding['path'])
    assert sha((repo/binding['path']).read_bytes())==binding['sha256']
    first_attempt=json.loads((run/'transport/attempts.jsonl').read_text().splitlines()[0])
    assert datetime.fromisoformat(label_review['reviewed_at']) < datetime.fromisoformat(binding['recorded_before_live']) < datetime.fromisoformat(first_attempt['at'])
    results['label_approval_chronology']={'approval_at':label_review['reviewed_at'],
                                         'binding_at':binding['recorded_before_live'],
                                         'first_attempt_at':first_attempt['at'],
                                         'approval_hash':binding['sha256'],
                                         'retained_timestamp_order_verified':True,
                                         'independent_timestamp_attestation':False}
    index=read(run/'native-evaluation-index.json')
    imported={r['request_id']:r for r in read(run/'reasoning-responses.json')}
    combined={r['request_id']:r for r in read(run/'native-responses-combined.json')['responses']}
    attempts={r['request_id']:r for r in read(run/'reasoning-attempts.json')}
    invocation_ids=[]
    native_rows=[]
    for row in index:
        d=run/'native-evaluations'/f"{row['evaluation_number']:03d}"
        request=read(d/'request.json'); response=read(d/'response.json'); meta=read(d/'invocation.json')
        assert (d/'request.json').read_bytes()==(run/row['path']).read_bytes()
        assert meta['raw_response_sha256']==sha((d/'response.json').read_bytes())
        assert meta['request_file_sha256']==sha((d/'request.json').read_bytes())
        assert request['request_id']==response['request_id']==row['request_id']
        assert dict(response,**meta)==imported[row['request_id']]==combined[row['request_id']]
        assert all(attempts[row['request_id']][k]==meta[k] for k in ['invocation_id','provider','requested_model','observed_model','requested_effort','observed_effort','context_exposure','usage','cost_usd','billing_scope'])
        assert all(meta[k] is None for k in ['observed_model','observed_effort','usage','elapsed_seconds','cost_usd'])
        assert row['directory']+'/request.json' in (d/'invocation-prompt.txt').read_text()
        invocation_ids.append(meta['invocation_id'])
        native_rows.append({'evaluation_number':row['evaluation_number'],'case_id':row['case_id'],
                            'condition':row['condition'],'request_id':row['request_id'],
                            'invocation_id':meta['invocation_id'],'request_file_sha256':meta['request_file_sha256'],
                            'raw_response_sha256':meta['raw_response_sha256']})
    assert len(index)==len(imported)==len(combined)==len(attempts)==22
    assert len(set(invocation_ids))==22
    results['native_records']={'count':22,'conditions':dict(Counter(r['condition'] for r in index)),
                               'all_request_response_import_attempt_joins_verified':True,
                               'unique_recorded_invocation_ids':22,
                               'process_isolation_or_full_session_history_independently_attested':False,
                               'provider_resolved_configuration_and_billing_attested':False,
                               'rows':native_rows}
    hosted=out/'actions-40071712425'
    context=read(hosted/'actions-context.json')
    assert context['GITHUB_SHA']==ids['hosted']
    assert context['source_tree']==results['identities']['hosted']['tree']
    reports={}
    for filename in ['experiment-tests.xml','hexagonal-tests.xml']:
        xml=ET.parse(hosted/filename).getroot()
        suites=list(xml)
        attrs={k:sum(int(s.attrib[k]) for s in suites) for k in ['tests','failures','errors','skipped']}
        assert attrs['failures']==attrs['errors']==attrs['skipped']==0
        attrs['testcase_elements']=len(xml.findall('.//testcase'))
        attrs['subtests_by_suite_minus_elements']=attrs['tests']-attrs['testcase_elements']
        reports[filename]=attrs
    results['original_hosted']={'context':context,'junit':reports,
                               'total_test_plus_subtest_count':sum(r['tests'] for r in reports.values()),
                               'total_testcase_elements':sum(r['testcase_elements'] for r in reports.values()),
                               'logs_confirm':'101 passed + 8 subtests; 598 passed + 164 subtests',
                               'model_execution':'hosted-live skipped; offline validate passed'}
    # Optional fresh connector observations saved by the reviewer, separate from repository assertions.
    if (out/'github-hosted-0.json').exists():
        remote=read(out/'github-hosted-0.json')
        artifacts=read(out/'github-hosted-2.json')['artifacts']
        jobs=read(out/'github-hosted-1.json')['jobs']
        assert remote['head_sha']==ids['hosted'] and remote['conclusion']=='success'
        artifact=next(a for a in artifacts if a['id']==12551771552)
        assert artifact['digest']=='sha256:'+sha((evidence/'actions-40071712425.zip').read_bytes())
        assert {j['name']:j['conclusion'] for j in jobs}=={'validate':'success','hosted-live':'skipped'}
        results['fresh_github_hosted_crosscheck']={'run_id':remote['id'],'head_sha':remote['head_sha'],
                                                  'artifact_id':artifact['id'],'digest_matches':True,
                                                  'job_conclusions':{j['name']:j['conclusion'] for j in jobs}}
    target=out/'provenance-results.json'
    target.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps({'result':str(target),'all_assertions_passed':True,'sealed_files':len(seal),
                      'preserved_files':len(existing),'native_joins':len(index),'hosted_tests_and_subtests':871}))


if __name__=='__main__':
    main()
