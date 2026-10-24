"""Post-hoc independent replay fail-closed controls using disposable copies.
Untouched archive passes first; full inventory checked before/after each mutation.
No change here is evidence of tampering in the original published archive.
"""
import argparse, copy, hashlib, json, pathlib, shutil, sys

def read(p): return json.loads(p.read_text())
def write(p,v): p.write_text(json.dumps(v,indent=2)+'\n')
def alter_json(root,path,fn):
    p=root/path; v=read(p); fn(v); write(p,v)

def decision(root):
    def f(v): next(r for r in v if r['policy']=='P1')['action']='reviewer-invalid-action'
    alter_json(root,'decisions.json',f)
def wire(root):
    item=read(root/'request-inventory.json')[0]
    a=read(root/item['assessment_path']); p=root/'transport/requests'/(a['request_id']+'.body.json')
    v=read(p); v['model']='jev-latest'; write(p,v)
def raw_response(root):
    p=root/'transport/attempts.jsonl'; rows=[json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    row=next(r for r in rows if r['status']=='ok' and 'judgment' in json.loads(r['raw_response_sanitized'])['answers'])
    rid=row['request_id']; raw=json.loads(row['raw_response_sanitized']); raw['model']='jev-latest'; row['raw_response_sanitized']=json.dumps(raw)
    p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    # Keep duplicate attempt ledgers and derived assessment mutually consistent,
    # so rejection must validate the typed raw response rather than duplicate text.
    tr=root/'transport/results'/(rid+'.json'); v=read(tr); v['attempts']=[r for r in rows if r['request_id']==rid]; write(tr,v)
    for item in read(root/'request-inventory.json'):
        a=root/item['assessment_path']; av=read(a)
        if av.get('request_id')==rid: av['attempts']=v['attempts']; write(a,av)
def probe_raw(root):
    row=next(r for r in read(root/'probes.json') if r['status']=='executed')
    p=root/row['raw_reference']; v=read(p); v['boundary']='reviewer changed boundary'; write(p,v)
def action_raw(root):
    row=read(root/'end-to-end.json')['rows'][0]
    p=root/row['raw_reference']; v=read(p); v['accepted_after']=not v['accepted_after']; write(p,v)
def action_summary(root):
    alter_json(root,'end-to-end.json',lambda v:v['by_policy']['P4'].update(correct_actual_acceptance=999))
def action_binding(root):
    alter_json(root,'end-to-end.json',lambda v:v['rows'][0].update(decision_sha256='0'*64))
def native_raw(root):
    alter_json(root,'native-evaluations/001/response.json',lambda v:v['answers'].update(judgment='supports'))
def native_invocation(root):
    alter_json(root,'native-evaluations/001/invocation.json',lambda v:v.update(invocation_id='/reviewer/nonexistent'))
def audit_result(root):
    def f(v): next(r for r in v if r['selected'])['independent_miss']=True
    alter_json(root,'audits.json',f)
def audit_probability(root):
    alter_json(root,'audits.json',lambda v:v[0].update(inclusion_probability=0))
def route_record(root):
    def f(v): next(r for r in v if r['policy']=='P4' and r.get('route')=='none')['route']='reasoning'
    alter_json(root,'decisions.json',f)
MUTATIONS=[decision,wire,raw_response,probe_raw,action_raw,action_summary,action_binding,native_raw,native_invocation,audit_result,audit_probability,route_record]
def main():
    p=argparse.ArgumentParser(); p.add_argument('--repo',type=pathlib.Path,required=True); p.add_argument('--original',type=pathlib.Path,required=True); p.add_argument('--out',type=pathlib.Path,required=True); a=p.parse_args()
    sys.path.insert(0,str(a.repo.resolve()))
    from experiments.jev_failure_correlation_v2.artifacts import inventory
    from experiments.jev_failure_correlation_v2.runner import score
    a.out.mkdir(parents=True,exist_ok=False); rows=[]
    expected=read(a.original/'ARTIFACTS.sha256.json')
    assert inventory(a.original)==expected, 'untouched archive must pass full seal'
    control=a.out/'untouched-control'; shutil.copytree(a.original,control); result=score(control)
    rows.append({'mutation':'untouched-control','semantic_score_accepted':True,'seal_matches_after_score':inventory(control)==expected,'raw_replay':result['raw_replay']})
    for fn in MUTATIONS:
        root=a.out/fn.__name__; shutil.copytree(a.original,root); fn(root)
        changed=[k for k,v in inventory(root).items() if expected.get(k)!=v]
        assert changed and inventory(root)!=expected
        try:
            result=score(root); accepted=True; error=None
        except Exception as exc:
            result=None; accepted=False; error=f'{type(exc).__name__}: {exc}'
        r={'mutation':fn.__name__,'changed_before_score':changed,'full_seal_rejects':True,'semantic_score_accepted':accepted,'error':error}
        if accepted:
            r['selected_output']={'end_to_end_by_policy':result.get('end_to_end',{}).get('by_policy'),'audit':result.get('audits'),'routing':result.get('routing_value')}
        rows.append(r); print(json.dumps({k:v for k,v in r.items() if k!='selected_output'}),flush=True)
    (a.out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
if __name__=='__main__':main()
