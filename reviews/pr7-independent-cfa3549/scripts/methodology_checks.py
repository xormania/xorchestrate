"""Post-hoc independent structural/representation checks; no provider access.

Run from the pinned repository root. Does not write to retained evidence.
"""
import collections
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
import zipfile

from experiments.jev_failure_correlation_v2.policies import baseline

root = Path('experiments/jev_failure_correlation_v2')
cases = json.loads((root / 'data/frozen/cases.json').read_text())['cases']
byid = {x['id']: x for x in cases}
z = zipfile.ZipFile(root / 'evidence/cloud-20261024-01.zip')
prefix = 'cloud-20261024-01/'
def read(name):
    return json.loads(z.read(prefix + name))

def independently_judge(packet):
    # Independent explicit comparison over the supported packet contract.
    # This checks the exported key's interpretation of its operands, not whether
    # extracting those operands from arbitrary incidents is easy or correct.
    values=[]
    for c in packet['facts']['checks']:
        if not c['observed'] or c['subject']['expected'] != c['subject']['observed']:
            values.append(None)
        elif c['operator'] == 'eq': values.append(c['left'] == c['right'])
        elif c['operator'] == 'in': values.append(c['left'] in c['right'])
        else: raise AssertionError(c['operator'])
    return 'contradicts' if False in values else 'unresolved' if None in values else 'supports'

result={'scope':'Post-hoc independent offline review of retained bytes; no new model calls or operational sampling.'}
result['initial_key_agreement']=sum(independently_judge(c['packet'])==c['initial_key']['judgment'] for c in cases)
result['outcome_key_agreement']=sum(independently_judge(c['outcome_observation'])==c['outcome_key']['judgment'] for c in cases)
result['initial_judgments']=dict(collections.Counter(c['initial_key']['judgment'] for c in cases))
result['missing_case_outcomes']=dict(collections.Counter(c['outcome_key']['judgment'] for c in cases if c['initial_key']['judgment']=='unresolved'))
result['groups_by_split']={s:len({c['group'] for c in cases if c['split']==s}) for s in sorted({c['split'] for c in cases})}
result['groups_crossing_splits']=[g for g in {c['group'] for c in cases} if len({c['split'] for c in cases if c['group']==g})>1]
assert result['initial_key_agreement']==result['outcome_key_agreement']==24
assert not result['groups_crossing_splits']

# All model requests retain the bounded initial packet. Post-probe payloads
# explicitly attach their new observation rather than silently replacing it.
inventory=read('request-inventory.json')
requests={r['request_id']:read(r['path']) for r in inventory}
base=[r for r in inventory if r['stage'] in ('initial','post_probe','adversarial')]
assert len(base)==72
assert all(requests[r['request_id']]['payload']['state']['evidence']==byid[r['case_id']]['packet'] for r in base)
result['staged_request_initial_packet_match']=len(base)
forbidden={'initial_key','outcome_key','answer_key','split','group','fault_injection','expected_outcome','repair_diff'}
def leaked(x):
    if isinstance(x,dict): return bool(set(x)&forbidden) or any(leaked(v) for v in x.values())
    if isinstance(x,list): return any(leaked(v) for v in x)
    return False
assert not any(leaked(r['payload']) for r in requests.values())
result['payload_forbidden_field_occurrences']=0
result['payload_scan_limit']='Field-name/identity checks do not prove absence of semantic leakage; explicit invariant/operator operands intentionally simplify this contract.'

probes=read('probes.json'); decisions=read('decisions.json'); packets=read('packets.json')
for r in base:
    if r['stage']=='initial': continue
    state=requests[r['request_id']]['payload']['state']
    observation=state.get('new_probe_observation')
    if observation and observation['status']=='executed':
        actual=independently_judge(observation['visible'])
        assert actual==byid[r['case_id']]['outcome_key']['judgment']
result['postprobe_requests_with_executed_observation']=sum(requests[r['request_id']]['payload']['state'].get('new_probe_observation',{}).get('status')=='executed' for r in base if r['stage']!='initial' and requests[r['request_id']]['payload']['state'].get('new_probe_observation') is not None)

# Prompt-representation facts: old missing declarations persist; no inference
# about causal model sensitivity is possible without a new paired comparison.
missing=[c['id'] for c in cases if c['initial_key']['judgment']=='unresolved']
result['stale_missing_declarations']=[{'case_id':cid,'merged_available_has_declaration':bool(packets[cid]['available']['facts'].get('declared_missing')),'merged_checks_all_observed':all(x['observed'] for x in packets[cid]['available']['facts']['checks']),'p3_action':next(r['action'] for r in decisions if r['case_id']==cid and r['policy']=='P3')} for cid in missing]

# New post-hoc ablation tests P0's dependency on pre-extracted operands;
# it does not evaluate Jev on raw-only packets or estimate extraction cost.
result['raw_only_ablation']=[]
for cid in ('c85b37db76aaa','cef7128badec1'):
    packet=copy.deepcopy(byid[cid]['packet'])
    original=baseline(packet)
    reordered=copy.deepcopy(packet);reordered['facts']['checks'].reverse()
    control=baseline(reordered)
    raw_only=copy.deepcopy(packet);raw_only['facts'].pop('checks')
    ablated=baseline(raw_only)
    assert original['action']==control['action']
    assert ablated['action']=='investigate'
    result['raw_only_ablation'].append({'case_id':cid,'true_local_judgment':byid[cid]['initial_key']['judgment'],'original_action':original['action'],'reversed_check_order_control_action':control['action'],'raw_only_action':ablated['action'],'observations_preserved':raw_only['observations']==packet['observations']})

p1={r['case_id']:r for r in decisions if r['policy']=='P1'}
robustness=read('robustness.json')
result['robustness']={}
for t in sorted({r['transform'] for r in robustness}):
    rows=[r for r in robustness if r['transform']==t]
    result['robustness'][t]={'n':len(rows),'judgment_correct':sum(r['passed'] for r in rows),'same_as_original_p1':sum(r['composed']['judgment']==p1[r['case_id']]['judgment'] for r in rows)}
result['robustness_groups']=sorted({byid[r['case_id']]['group'] for r in robustness})
result['robustness_probe_families']=sorted({byid[r['case_id']]['probe_id'] for r in robustness})

false_clear_ids=[r['case_id'] for r in decisions if r['policy']=='P4' and r['action']=='accept' and byid[r['case_id']]['outcome_key']['action']!='accept']
audit=read('audits.json')
result['false_clears']=[{'case_id':cid,'group':byid[cid]['group'],'native_judgment_confidence':next(r['native_confidence']['judgment'] for r in decisions if r['policy']=='P4' and r['case_id']==cid),'selected_for_audit':next(r['selected'] for r in audit if r['case_id']==cid),'initial_declared_missing':bool(byid[cid]['packet']['facts'].get('declared_missing'))} for cid in false_clear_ids]
strata=collections.defaultdict(list)
for r in audit: strata[r['stratum']].append(r)
probability=1
parts=[]
for name,rows in sorted(strata.items()):
    n=len(rows);k=sum(r['selected'] for r in rows);e=sum(r['case_id'] in false_clear_ids for r in rows)
    p=math.comb(n-e,k)/math.comb(n,k) if k<=n-e else 0
    probability*=p
    if e: parts.append({'stratum':name,'population':n,'sample':k,'known_errors':e,'probability_miss_all':p})
result['audit_conditional_probability_miss_both_known_errors']={'value':probability,'strata':parts,'interpretation':'Exact probability under frozen stratified SRS design and known controlled outcome population, not a production error probability or CI.'}

if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
