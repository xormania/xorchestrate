#!/usr/bin/env python3
"""Keyless independent arithmetic/ablation from retained PR7 raw ledgers.

Does not import experiment scoring/policy code or read evaluation.json/report.json.
No network, model, or behavioral execution. Keys remain retained adjudication,
not independently re-created product truth. Post-hoc ablations are labeled.
"""
import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import operator
import random


def read(p):
    return json.loads(p.read_text())


def jlines(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


SCENARIOS = {
    'low_stakes': dict(false_acceptance=2., duplicate_effect=5., wasted_investigation=1., low_impact=.1, unnecessary_rejection=.5, investigation_action=.25, defer_action=.1),
    'consequential': dict(false_acceptance=100., duplicate_effect=500., wasted_investigation=10., low_impact=.1, unnecessary_rejection=20., investigation_action=2., defer_action=5.),
}
OPS = {'eq':operator.eq,'le':operator.le,'ge':operator.ge,'in':lambda a,b:a in b,'not_in':lambda a,b:a not in b}


def fact(c):
    if not c.get('observed',True) or c.get('subject',{}).get('expected') != c.get('subject',{}).get('observed'):
        return None
    if c.get('operator') not in OPS or not {'left','right'} <= c.keys(): return None
    try: return bool(OPS[c['operator']](c['left'],c['right']))
    except (TypeError, ValueError, KeyError): return None


def action(packet):
    f=packet.get('facts',{}); checks=f.get('checks',[]); values=[fact(c) for c in checks]
    if not f.get('policy_defined',True): return 'defer'
    if False in values: return 'reject'
    if not values or None in values or not set(f.get('required_evidence',[])) <= set(f.get('observed_evidence',[])): return 'investigate'
    return 'accept'


def outcome_loss(case, a, scenario):
    if a == case['outcome_key']['action']: return 0.
    if a == 'accept': return scenario[case['packet']['consequences']['class']]
    return scenario[{'reject':'unnecessary_rejection','investigate':'investigation_action','defer':'defer_action'}[a]]


def status(r, cases):
    if r['status']!='ok' or r['judgment'] not in ('supports','contradicts') or r['action'] not in ('accept','reject'): return 'unresolved'
    return 'right' if r['action']==cases[r['case_id']]['outcome_key']['action'] else 'wrong'


def cost(rows, attempts, probes):
    rids={v for r in rows for v in r.get('request_ids',[])}
    pids={v for r in rows for v in r.get('probe_ids',[])}
    selected=[a for a in attempts if a.get('request_id',a.get('assessment_id')) in rids]
    selected_probes=[p for p in probes if p['record_id'] in pids]
    raw=[]
    for a in selected:
        if a.get('raw_response_sanitized'): raw.append(json.loads(a['raw_response_sanitized']))
    native=[a for a in selected if not a.get('raw_response_sanitized')]
    input_tokens=sum(a.get('usage',{}).get('input_tokens',0) for a in raw)
    return dict(requests=len(selected), jev_calls=len(raw), native_calls=len(native), jev_input_tokens=input_tokens,
        jev_list_price_usd=input_tokens*.042/1_000_000, probes=len(selected_probes),
        probe_seconds=sum(p['elapsed_seconds'] for p in selected_probes),
        missing_request_ids=sorted(rids-{a.get('request_id',a.get('assessment_id')) for a in selected}),
        missing_probe_ids=sorted(pids-{p['record_id'] for p in selected_probes}))


def summary(rows,cases):
    return dict(n=len(rows), actions=dict(Counter(r['action'] for r in rows)),
        decisive_correct_wrong_unresolved=dict(Counter(status(r,cases) for r in rows)),
        correct_actions=sum(r['action']==cases[r['case_id']]['outcome_key']['action'] for r in rows),
        outcome_loss_assumed_usd={n:sum(outcome_loss(cases[r['case_id']],r['action'],s) for r in rows) for n,s in SCENARIOS.items()})


def compare(before, after, cases, ok_only=False):
    a={r['case_id']:r for r in before}; b={r['case_id']:r for r in after}
    ids=sorted(a.keys() & b.keys())
    if ok_only: ids=[cid for cid in ids if a[cid]['status']=='ok' and b[cid]['status']=='ok']
    transitions=Counter(status(a[cid],cases)+'->'+status(b[cid],cases) for cid in ids)
    return dict(n=len(ids), transitions=dict(transitions), action_changes=[dict(case_id=cid,before=a[cid]['action'],after=b[cid]['action']) for cid in ids if a[cid]['action']!=b[cid]['action']],
        gross_loss_reduction_assumed_usd={n:sum(outcome_loss(cases[cid],a[cid]['action'],s)-outcome_loss(cases[cid],b[cid]['action'],s) for cid in ids) for n,s in SCENARIOS.items()})


def main(args):
    main=args.main; supplement=args.supplement
    cases={c['id']:c for c in read(main/'cases.json')}
    rows=read(main/'decisions.json'); probes=read(main/'probes.json'); packets=read(main/'packets.json')
    attempts=jlines(main/'transport/attempts.jsonl')+read(main/'reasoning-attempts.json')
    by_policy={name:[r for r in rows if r['policy']==name] for name in sorted({r['policy'] for r in rows})}
    manifest=read(main/'manifest.json'); paired=set(manifest['paired_case_ids'])
    supp_rows=read(supplement/'decisions.json'); supp_probes=read(supplement/'probes.json'); plan=read(supplement/'plan.json')
    result={'scope':'Independent stdlib recomputation; no evaluation/report summaries imported. All USD losses analyst scenarios; actual invoices/native costs unknown.',
        'main_sha256':{n:sha256((main/n).read_bytes()).hexdigest() for n in ['cases.json','decisions.json','probes.json','packets.json','transport/attempts.jsonl','reasoning-attempts.json']},
        'policies':{p:{**summary(rs,cases),'resource_dependencies':cost(rs,attempts,probes)} for p,rs in by_policy.items()},
        'comparisons':{},'paired':{},'supplement':{},'posthoc_no_new_evidence_ablation':{}}
    for before,after in [('P0','P0_probe'),('P0','P1'),('P1','P2'),('P2','P3'),('P3','P4'),('P0','P4'),('P0_probe','P2'),('never','always'),('reasoning_only','always')]:
        result['comparisons'][before+'->'+after]={'all_known_actions':compare(by_policy[before],by_policy[after],cases), 'published_status_ok_denominator':compare(by_policy[before],by_policy[after],cases,True)}
    for p in by_policy:
        selected=[r for r in by_policy[p] if r['case_id'] in paired]
        result['paired'][p]={**summary(selected,cases),'resource_dependencies':cost(selected,attempts,probes)}
    raw=[json.loads(a['raw_response_sanitized']) for a in attempts if a.get('raw_response_sanitized')]
    assert all(a['usage']==json.loads(a['raw_response_sanitized'])['usage'] for a in attempts if a.get('raw_response_sanitized'))
    result['collection']=dict(jev_calls=len(raw),jev_input_tokens=sum(a['usage']['input_tokens'] for a in raw),jev_output_tokens=sum(a['usage']['output_tokens'] for a in raw),jev_list_price_usd=sum(a['usage']['input_tokens'] for a in raw)*.042/1_000_000,native_calls=sum(not a.get('raw_response_sanitized') for a in attempts),native_actual_cost_usd=None,actual_total_usd=None)
    for stage in ['P0_probe','P2','P4','independent_random_audit']:
        ps=[p for p in probes if p['policy']==stage]
        result.setdefault('probe_stages',{})[stage]=dict(records=len(ps),statuses=dict(Counter(p['status'] for p in ps)),seconds=sum(p['elapsed_seconds'] for p in ps))
    p3=[r for r in by_policy['P3'] if r['case_id'] in paired]
    expected_random=sorted(random.Random(4017).sample(sorted(paired),5))
    value_map=manifest['plan']['valuation_scenario']['consequence_class_units']
    expected_simple=sorted(sorted(paired,key=lambda cid:(-int(any(fact(c) is None for c in packets[cid]['available']['facts']['checks'])),-sum(fact(c) is None for c in packets[cid]['available']['facts']['checks']),-value_map[packets[cid]['available']['consequences']['class']],cid))[:5])
    assert expected_random==plan['random_selection'] and expected_simple==plan['simple_selection']
    result['supplement']['selection_verified']={'random':expected_random,'simple':expected_simple,'zero_missing_checks_all_paired':all(all(fact(c) is not None for c in packets[cid]['available']['facts']['checks']) for cid in paired)}
    prefix_ids={v for r in p3 for v in r.get('probe_ids',[])}
    result['supplement']['common_prefix']=cost(p3,attempts,probes)
    for name,rs in [('P4',[r for r in by_policy['P4'] if r['case_id'] in paired]),('random_probe',[r for r in supp_rows if r['policy']=='random_probe']),('simple_probe',[r for r in supp_rows if r['policy']=='simple_probe'])]:
        extra_ids={v for r in rs for v in r.get('probe_ids',[])}-prefix_ids
        extra=[p for p in probes+supp_probes if p['record_id'] in extra_ids]
        seconds=sum(p['elapsed_seconds'] for p in extra)
        comp=compare(p3,rs,cases)
        result['supplement'][name]={**summary(rs,cases),'vs_P3':comp,'additional_probe_calls':len(extra),'additional_probe_seconds':seconds,'new_semantic_check_lists':sum(p['visible']['facts']['checks']!=packets[p['case_id']]['available']['facts']['checks'] for p in extra if p['status']=='executed'),
            'net_at_001_usd_per_second_before_other_costs':{n:g-seconds*.01 for n,g in comp['gross_loss_reduction_assumed_usd'].items()},
            'per_probe':[dict(case_id=p['case_id'],probe_id=p['probe_id'],status=p['status'],seconds=p['elapsed_seconds']) for p in extra]}
    interpretations=[]
    for cid,pack in sorted(packets.items()):
        pre=pack['available']; a=action(pre)
        interpretations.append(dict(case_id=cid,action=a,truth=cases[cid]['outcome_key']['action'],correct=a==cases[cid]['outcome_key']['action'],paired=cid in paired,checks_known=all(fact(c) is not None for c in pre['facts']['checks']),retained_stale_missing_note=pre['facts'].get('declared_missing')))
    result['posthoc_no_new_evidence_ablation']=dict(classification='Post-hoc independent structured reinterpretation; no new observations or model calls; not a prespecified measured policy.',cases=len(interpretations),correct=sum(r['correct'] for r in interpretations),paired_correct=sum(r['correct'] and r['paired'] for r in interpretations),retained_stale_missing_note_cases=[r['case_id'] for r in interpretations if r['retained_stale_missing_note']],rows=interpretations)
    # Validate the physical repeated-probe comparison without equating it to information gain.
    # Reviewer-created post-hoc controls prove the ablation reads the supplied
    # check operands/identity rather than selecting actions from outcome labels.
    controls=[]
    for cid,pack in sorted(packets.items()):
        original=pack['available']
        absent=deepcopy(original)
        for c in absent['facts']['checks']: c['observed']=False
        foreign=deepcopy(original)
        for c in foreign['facts']['checks']: c['subject']={'expected':'current','observed':'foreign'}
        healthy=deepcopy(original)
        for c in healthy['facts']['checks']:
            c.update(left=True,right=True,operator='eq',observed=True,subject={'expected':'current','observed':'current'})
        fault=deepcopy(healthy);fault['facts']['checks'][0]['left']=False
        got={'missing':action(absent),'foreign_subject':action(foreign),'healthy':action(healthy),'fault':action(fault)}
        assert got=={'missing':'investigate','foreign_subject':'investigate','healthy':'accept','fault':'reject'}
        controls.append({'case_id':cid,**got})
    result['posthoc_interpreter_controls']={'classification':'Reviewer-created synthetic controls; not new incident samples or provider responses.','n':len(controls),'checks':4*len(controls),'rows':controls}
    result['P4_same_check_lists_before_after']=dict(executed=sum(p['policy']=='P4' and p['status']=='executed' for p in probes),same=sum(p['policy']=='P4' and p['status']=='executed' and p['visible']['facts']['checks']==packets[p['case_id']]['available']['facts']['checks'] for p in probes))
    inventory=read(main/'reasoning-inventory.json')
    native_views=[]
    for cid in sorted(paired):
        records={r['condition']:read(main/r['path'])['payload']['state'] for r in inventory if r['case_id']==cid}
        blind=records['reasoning_only']; fallback=records['fallback']
        native_views.append(dict(case_id=cid,same_original_evidence=blind['evidence']==fallback['evidence'],fallback_has_jev_assessment='previous_assessment' in fallback,fallback_has_probe=fallback.get('new_probe_observation') is not None,initial_action=action(blind['evidence']),postP3_action=action(packets[cid]['available'])))
    result['native_input_comparison']=native_views
    e2e=read(main/'end-to-end.json')['rows']
    observed=[]
    for r in e2e:
        raw=read(main/r['raw_reference'])
        assert sha256((main/r['raw_reference']).read_bytes()).hexdigest()==r['raw_sha256']
        accepted=raw['after']['accepted']; should=raw['independent_assessment']['conclusion']['outcome']=='pass'
        observed.append(dict(case_id=r['case_id'],policy=r['policy'],action=r['action'],accepted_after=accepted,independent_should_accept=should,correct=accepted==should))
    result['actual_acceptance_outcomes']=dict(executions=len(observed),cases=len({r['case_id'] for r in observed}),groups=len({cases[r['case_id']]['group'] for r in observed}),all_correct=all(r['correct'] for r in observed),rows=observed)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'output':str(args.output),'collection':result['collection'],'P4_same_check_lists_before_after':result['P4_same_check_lists_before_after'],'ablation_correct':result['posthoc_no_new_evidence_ablation']['correct'],'supplement_net':{p:result['supplement'][p]['net_at_001_usd_per_second_before_other_costs'] for p in ['P4','random_probe','simple_probe']},'e2e_all_correct':result['actual_acceptance_outcomes']['all_correct']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--main',type=Path,required=True);p.add_argument('--supplement',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    main(p.parse_args())
