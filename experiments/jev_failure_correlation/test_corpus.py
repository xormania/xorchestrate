"""Checks that fail when extraction corrupts chronology, evidence or separation."""
import json
import re
from pathlib import Path
from experiments.jev_failure_correlation.corpus import canonical, digest

ROOT=Path(__file__).parent
DATA=ROOT/'data'
def read(name):return json.loads((DATA/name).read_text())
def named(name):
 p=read('provenance.json')['cases'];ident=next(k for k,v in p.items() if v.get('historical_name')==name)
 return next(c for c in read('cases.json')['cases'] if c['id']==ident)

def test_frozen_bytes_and_material_identity():
 freeze=read('freeze.json')
 for path,h in freeze['files'].items():assert digest((DATA/path).read_bytes())==h
 for c in read('cases.json')['cases']:
  assert digest(canonical(c['packet']))==read('provenance.json')['cases'][c['id']]['packet_sha256']
  changed={**c['packet'],'extra_observation':True}
  assert digest(canonical(changed))!=digest(canonical(c['packet']))

def test_answer_key_separate_and_no_reviewer_leakage():
 cases=read('cases.json');key=read('answer_key.json')
 assert {c['id'] for c in cases['cases']}==set(key['cases'])
 text=json.dumps(cases)
 for forbidden in ['defect_reproduced','known_defect','expected_oracle','EXPECTED_VIOLATION','answer_key','exception_origin','material_context_changed']:
  assert forbidden not in text
 assert not re.search(r'\bF(?:0[1-9]|10)\b|review/tracks/|stale_decision|leaked_sections',text)


def test_family_splits_and_independent_variants():
 key=read('answer_key.json')['cases'];splits=read('splits.json')
 for family in {x['family'] for x in key.values()}:
  assert len({x['split'] for x in key.values() if x['family']==family})==1
 assert set(splits['development_families']).isdisjoint(splits['heldout_families'])
 assert set(x['family'] for x in key.values())=={f'F{i:02}' for i in range(1,11)}
 for case in ['v01','v02','v03','v04']:assert key[case]['split']=='development'
 assert key['v03']['kind']=='valid_control' and key['v04']['kind']=='environment_failure'


def test_arguments_preserve_disagreement_and_control_agreement():
 bad=named('F08-arguments')['packet']['observations'];good=named('F08-agreement')['packet']['observations']
 assert bad[0]['call_id']==bad[1]['call_id'] and bad[0]['destination_actor']!=bad[1]['destination_actor']
 assert good[0]['destination_actor']==good[1]['destination_actor']
 for name in ['F08-arguments','F08-agreement']:
  c=named(name);refs=read('provenance.json')['cases'][c['id']]['raw_refs'];assert len(refs)>=4


def test_attempt_order_retained():
 obs=named('F09-attempts')['packet']['observations'];attempts=[x for x in obs if x['event']=='attempt']
 assert len(attempts)>=2 and attempts[-1]['status']=='unknown'
 assert attempts[-1]['sequence']>attempts[0]['sequence']
 assert len({(a['message_id'],a['recipient']) for a in attempts})==1
 good=[x for x in named('F09-original-attempt')['packet']['observations'] if x['event']=='attempt']
 assert good and all(a['status']=='submitted' for a in good)


def test_timing_has_raw_chronology():
 obs=named('F10-timing')['packet']['observations'];events={o['event']:o for o in obs}
 assert events['challenge']['at']<events['work_completed']['at']<events['reply']['at']
 assert events['timing_observation']['reply_before_work_completed'] is False
 assert events['checker_run']['failed_cases']==0


def test_unrelated_errors_are_not_intended_credit():
 key=read('answer_key.json')['cases']
 for name in ['F07-unrelated-timeout','F07-runtime-missing-dependency']:
  c=named(name);assert key[c['id']]['mechanism']==['assertion_proxy']
  refs=read('provenance.json')['cases'][c['id']]['raw_refs'];raw=read('raw_observations.json')['records']
  xml=[raw[r]['text'] for r in refs if raw[r]['source'].endswith('mutant.xml')]
  assert len(xml)==1 and ('TimeoutError' in xml[0] or 'ModuleNotFoundError' in xml[0])
 assert key[named('F07-collection-missing-dependency')['id']]['kind']=='environment_failure'
 assert key[named('F07-skipped')['id']]['kind']=='incomplete'


def test_pair_binding_and_ambiguity():
 cases={c['id'] for c in read('cases.json')['cases']};keys=read('answer_key.json')['relationships']
 for pair in read('pairs.json')['pairs']:
  assert pair['left'] in cases and pair['right'] in cases and pair['id'] in keys
  if 'same_mechanism' in keys[pair['id']]['relationship']:assert 'insufficient' in keys[pair['id']]['relationship']


def test_archive_integrity_result():
 x=read('provenance.json')['archive_integrity']
 assert x['files_checked']==1831 and not x['errors']
 assert x['owner_brief_sha256']=='41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad'
