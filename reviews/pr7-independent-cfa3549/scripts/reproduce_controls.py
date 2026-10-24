"""Post-hoc keyless reviewer controls against a pinned repository.
Use --summarize-existing only to aggregate already-retained probe/action records;
that mode does not execute behavior and is clearly recorded in the result.
"""
import argparse, json, pathlib, platform, sys

def load(path): return json.loads(path.read_text())
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',type=pathlib.Path,required=True)
    ap.add_argument('--out',type=pathlib.Path,required=True)
    ap.add_argument('--original',type=pathlib.Path,required=True)
    ap.add_argument('--summarize-existing',action='store_true')
    args=ap.parse_args(); sys.path.insert(0,str(args.repo.resolve()))
    from experiments.jev_failure_correlation_v2.corpus import load_corpus
    from experiments.jev_failure_correlation_v2.probes import PROBES, execute_probe, execute_action
    from experiments.jev_failure_correlation_v2.policies import baseline, route, merge_observation
    from experiments.jev_failure_correlation_v2.contracts import compose
    cases=load_corpus(); keys={c['id']:c for c in cases}
    decisions=load(args.original/'decisions.json')
    retained_probes={p['record_id']:p for p in load(args.original/'probes.json')}
    args.out.mkdir(parents=True,exist_ok=args.summarize_existing)
    results=[]
    for pid in PROBES:
        for variant in ['healthy','fault','setup','helper','timeout']:
            ordinary=variant in ['healthy','fault']
            case=next(c for c in cases if c['probe_id']==pid and (not ordinary or c['scenario']==variant))
            destination=args.out/f'{pid}-{variant}'
            result=load(destination/'probe.json') if args.summarize_existing else execute_probe(case,pid,destination,control_mode=None if ordinary else variant)
            if ordinary:
                expected=all(x['satisfied'] for x in result['assertions']) if variant=='healthy' else any(x['satisfied'] is False for x in result['assertions'])
                correct=result['status']=='executed' and result['exercised'] and result['decisive'] and expected
            else:
                correct=result['status']=='not_exercised' and not result['exercised'] and not result['decisive'] and not result['assertions']
            results.append({'probe':pid,'variant':variant,'status':result['status'],'correct_exercised_control':correct,'record':result})
    actions=[]
    for variant,action in [('fault','accept'),('fault','reject'),('healthy','accept'),('healthy','investigate')]:
        case=next(c for c in cases if c['probe_id']=='p_context' and c['scenario']==variant)
        destination=args.out/f'action-{variant}-{action}'
        actions.append(load(destination/'action.json') if args.summarize_existing else execute_action(case,action,destination))
    selected=[]
    for d in decisions:
        c=keys[d['case_id']]
        if d['policy']=='P4' and d['action']=='accept' and c['outcome_key']['action']=='reject':
            current_packet=c['packet']
            for pid in d['probe_ids']: current_packet=merge_observation(current_packet,retained_probes[pid])
            selected.append({'case_id':d['case_id'],'probe_id':c['probe_id'],'judgment_confidence':d['native_confidence']['judgment'],'raw_route':d.get('route'),'route_recomputed':route(c['packet'],d['assessment']),'structured_initial_control':baseline(c['packet'])['action'],'structured_postprobe_control':baseline(current_packet)['action'],'acquired_evidence':d['acquired_evidence'],'available_postprobe_facts':current_packet['facts']})
    budget=[{'status':s,'result':compose({'status':s,'answers':{'judgment':'supports'}})} for s in ['budget_exhausted','provider_failure','pending','unresolved']]
    summary={'reviewer_created_post_hoc':True,'python':platform.python_version(),'aggregation_mode':'retained_records' if args.summarize_existing else 'fresh_behavior_then_aggregation','probe_checks':results,'action_checks':actions,'retained_false_clears':selected,'budget_controls':budget,'totals':{'probe_variants':len(results),'passed':sum(r['correct_exercised_control'] for r in results),'not_exercised_ordinary_variants':[{'probe':r['probe'],'variant':r['variant'],'reason':r['record'].get('reason')} for r in results if r['variant'] in ['healthy','fault'] and r['status']!='executed']}}
    (args.out/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary['totals']))
    assert actions[0]['consequential_false_accept'] and actions[1]['behavioral_outcome_correct'] and actions[2]['behavioral_outcome_correct'] and actions[3]['unnecessary_withholding']
    assert all(b['result']['action']=='defer' for b in budget)
    assert not summary['totals']['not_exercised_ordinary_variants'], 'Some ordinary controls were not exercised; preserve as blocked, not successful detection.'
    assert all(r['correct_exercised_control'] for r in results)
if __name__=='__main__': main()
