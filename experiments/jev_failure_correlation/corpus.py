"""Extract a versioned calibration corpus from the immutable independent review.

Only explicitly selected observations enter packets. Original selected records,
source selectors and hashes remain separately auditable. No API calls are made.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

VERSION = '1.1.0'
CANDIDATE = 'b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a'
DEVELOPMENT = {'F01', 'F02', 'F04', 'F07'}
PROBES = {
 'p1': 'Compare an identical replay with a fresh read before and after owner requirement revision.',
 'p2': 'Hold the response after an effect or callback is observed; replace the connection, release it, and inspect identities, effects, and recovery.',
 'p3': 'Compare original native arguments, executed callback arguments, and ordered attempt/reply records against the checker result.',
 'p4': 'Run the target assertion in isolation with a passing control, an intended assertion failure, and a helper exception; inspect the actual exception origin.',
 'p5': 'Compare authorized and denied work reads with delivery inspection using distinct synthetic work data.',
 'p6': 'Hold two distinct callbacks, release one, inspect activity and pending work, then release the other.',
 'p7': 'Change an evaluator-consumed dependency and a cosmetic title separately; compare reused and fresh assessments.',
 'none': 'No useful additional discriminating probe is supported by this packet.',
 'insufficient': 'Collect the missing observations before choosing a discriminating probe.',
}

def digest(data):
 return hashlib.sha256(data).hexdigest()

def canonical(value):
 return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

def dump(path, value):
 path.parent.mkdir(parents=True, exist_ok=True)
 path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')

def verify_archive(root):
 manifest = json.loads((root/'review/package-manifest.json').read_text())
 errors=[]
 for item in manifest['files']:
  p=root/item['path']
  if not p.is_file(): errors.append({'path':item['path'],'error':'missing'});continue
  b=p.read_bytes()
  if len(b)!=item['bytes'] or digest(b)!=item['sha256']: errors.append({'path':item['path'],'error':'hash_or_size'})
 brief=digest((root/'review/owner-brief.md').read_bytes())
 if brief!='41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad':errors.append({'error':'owner_brief_hash'})
 return {'candidate':manifest['candidate'],'files_checked':len(manifest['files']),'errors':errors,'owner_brief_sha256':brief,'manifest_sha256':digest((root/'review/package-manifest.json').read_bytes())}

class Builder:
 def __init__(self,root):
  self.root=root;self.cases=[];self.answers={};self.sources={};self.raw={};self.map={};self.ids={}
 def source(self,path,selector=None):
  p=self.root/path;raw=json.loads(p.read_text());self.sources[path]={'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size}
  value=raw
  for part in selector or []:value=value[part]
  key='s'+digest(canonical([path,selector]))[:14]
  self.raw[key]={'source':path,'selector':selector or [],'value':value}
  return value,key
 def add(self,name,family,kind,observations,mechanism,probe,refs,requirements,rationale,omitted='Unselected configuration, unrelated events, cleanup, random transport metadata, reviewer labels and summary flags.'):
  ident='c'+digest(name.encode())[:10];self.ids[name]=ident
  packet={'observations':observations,'probe_options':PROBES}
  self.cases.append({'id':ident,'packet':packet})
  self.answers[ident]={'family':family,'split':'development' if family in DEVELOPMENT else 'heldout','kind':kind,'mechanism':mechanism if isinstance(mechanism,list) else [mechanism],'probe':probe if isinstance(probe,list) else [probe],'requirements':requirements,'rationale':rationale}
  self.map[ident]={'historical_name':name,'candidate':CANDIDATE,'raw_refs':refs,'extraction':'Explicit field selection and event framing in corpus.py. Observed numbers, booleans, codes and chronological relations are unchanged. Reviewer labels removed; neutral identity replacements are recorded below.','omitted':omitted,'packet_sha256':digest(canonical(packet))}
  return ident

def build(root,out,additional=None):
 b=Builder(root)
 integrity=verify_archive(root)
 if integrity['errors']:raise ValueError(integrity)
 delivery='review/tracks/delivery_configuration/cross-work-leak.json'
 for idx in [0,1]:
  x,s=b.source(delivery,[idx]);resp=x['response']
  obs=[{'event':'configuration','store':x['store'],'actor':'limited','authorized_work':x['allowed_work_id'],'denied_work':x['private_work_id']},*({'action':d['action'],'work_id':x['private_work_id'],'code':d['code']} for d in x['direct_denied_controls']),{'action':'get_delivery','work_id':x['allowed_work_id'],'returned_evidence':[{'work_id':e['work_id'],'result':{'value':'marker-A'}} for e in resp['evidence']],'returned_messages':[{'work_id':m['work_id'],'body':{'value':'marker-A'} if 'secret' in m['body'] else m['body']} for m in resp['messages']]}]
  b.add('F01-'+x['store'],'F01','product_defect',obs,'authority_scope','p5',[s],['H5','H3','A2'],'An authorized enclosing read returns data bound to a work explicitly denied to this actor.')
  if idx==0:b.add('F01-denied-read','F01','valid_control',obs[:3],'legitimate','none',[s],['H5'],'Direct denied reads return denied, without data.')
 bridge='review/tracks/concurrency_lifecycle/composed-bridge-results.json'
 for idx in [0,1]:
  x,s=b.source(bridge,[idx]);old=x['old_session'];new=x['new_session'];evt=x['host_events'][0]
  obs=[{'event':'configuration','store':'sqlite','actor_permissions':{'old-actor':{'record_result':False},'new-actor':{'record_result':True}}},{'event':'call_observed','actor':old['actor_id'],'work_id':old['work_id'],'incarnation':old['backend']['incarnation'],'call_id':evt['call_id'],'action':evt['action'],'at':evt['at']},{'event':'connection_replacement','replaced':x['replacement'],'actor':new['actor_id'] if new else old['actor_id'],'work_id':new['work_id'] if new else old['work_id'],'incarnation':new['backend']['incarnation'] if new else old['backend']['incarnation']},{'event':'callback_released','outcome':x['send_outcome']},{'event':'persisted_results','records':[{'work_id':e['work_id'],'actor':e['source']['actor'],'at':e['created_at']} for w in x['work_snapshots'].values() for e in w['evidence']]}]
  b.add('F02-'+str(idx),'F02','product_defect' if idx else 'valid_control',obs,'authority_scope' if idx else 'legitimate','p2' if idx else 'none',[s],['H5','H7','H4'],'Old denied callback receives replacement principal/work and writes evidence.' if idx else 'Denied callback writes no evidence; outer operation conservatively remains unknown.')
 late='review/tracks/concurrency_lifecycle/late-effect-results.json'
 for idx in [0,1]:
  x,s=b.source(late,[idx]);first=x['first_outcome'];outcome={k:v for k,v in first.items() if k!='result'} if 'result' not in first else {'output':first['result']['output']}
  obs=[{'event':'effect_applied','count':x['first_effect_count']},{'event':'response_held','connection_replaced':x['replacement'],'additional_send':x['pending_control']},{'event':'response_released',**outcome},{'event':'host_retained_results','snapshot_time':'after_next_send','effects':[{'status':e['status'],'result':e['result']} for e in x['host_state']['effects'].values()]},{'event':'next_send','count':x['final_effect_count'],'output':x['next_effect_outcome']['result']['output']}]
  b.add('F03-'+str(idx),'F03','product_defect' if idx else 'valid_control',obs,'post_effect_uncertainty' if idx else 'legitimate','p2' if idx else 'none',[s],['H7','H4'],'Applied effect becomes failed after reconnect while host retains completion.' if idx else 'Completion remains known before a separate additional send.')
 replay='review/tracks/architecture_acceptance/protocol_replay/results.json'
 for idx in range(3):
  x,s=b.source(replay,['results',idx]);obs=[{'event':'configuration','adapter':x['protocol']},{'event':'initial_read','requirement_revision':x['baseline_requirement_revision'],'accepted':x['baseline_accepted']},{'event':'owner_revision','requirement_revision':2},{'event':'replay_read','requirement_revision':x['adapter_replay_requirement_revision'],'accepted':x['adapter_replay_accepted'],'current':x['adapter_replay_current_flags'][0]},{'event':'fresh_read','requirement_revision':x['fresh_adapter_requirement_revision'],'accepted':x['fresh_adapter_call_accepted']},{'event':'direct_replay','code':x['direct_invoke_native_replay_error']}]
  b.add('F04-'+x['protocol'],'F04','product_defect',obs,'obsolete_result','p1',[s],['B1','B2','H7','A2'],'Repeated semantic call returns old accepted/current basis while fresh same-adapter and direct reads reject.')
  if idx==0:b.add('F04-fresh','F04','valid_control',[obs[0],obs[1],obs[2],obs[4],obs[5]],'legitimate','none',[s],['B1','B2'],'Fresh call reports revision 2 and direct duplicate is rejected.')
 assess='review/tracks/architecture_acceptance/assessment_currentness.json'
 for idx in [0,4]:
  x,s=b.source(assess,['cases',idx]);obs=[{'event':'configuration','store':x['backend'],'schedule':x['change_time'],'acceptance_requires_assessment':True},{'event':'assessment','work_version':x['old_assessment_work_version'],'context':{},'conclusion':x['old_assessment_conclusion']},{'event':'context_update','work_version':x['current_work_version'],'context':{'condition-A':x['current_context']['known_defect']},'requirements_revision':x['requirements_revision']},{'event':'reuse_assessment','accepted':x['stale_decision_accepted'],'issues':x['stale_decision_issues']},{'event':'fresh_assessment','conclusion':x['fresh_assessment_conclusion'],'accepted':x['fresh_decision_accepted']}]
  if x['change_time']=='during_assessment':
   obs=[obs[0],{'event':'assessment_inputs_captured','work_version':x['old_assessment_work_version'],'context':{}},obs[2],obs[1],*obs[3:]]
  b.add('F05-'+str(idx),'F05','product_defect',obs,'obsolete_result','p7',[s],['B2','A2'],'Material consumed context differs, fresh assessment rejects, reused assessment still accepts.')
 x,s=b.source('review/tracks/architecture_acceptance/composed_assessment_currentness.json',['results',0])
 definition='review/tracks/architecture_acceptance/composed_assessment_currentness.py'
 b.sources[definition]={'sha256':digest((root/definition).read_bytes()),'bytes':(root/definition).stat().st_size}
 definition_key='s'+digest(definition.encode())[:14];b.raw[definition_key]={'source':definition,'selector':[],'text':(root/definition).read_text()}
 b.add('F05-title','F05','valid_control',[{'event':'configuration','composition':['CLIProvider','ToolEvaluator','SQLiteStore'],'acceptance_requires_assessment':True},{'event':'assessment','conclusion':'pass','accepted':x['baseline_accepted']},{'event':'title_update','changes':{'title':'Routine display title'},'context_before':{},'context_after':{}},{'event':'reuse_assessment','accepted':x['old_assessment_reaccepted']},{'event':'fresh_assessment','conclusion':x['fresh_outcome'],'accepted':x['fresh_accepted']}],'legitimate','none',[s,definition_key],['B2','A2'],'Cosmetic title changes leave evaluator dependency and both outcomes unchanged.')
 x,s=b.source('review/tracks/concurrency_lifecycle/probe-results.json',[2])
 obs=[{'event':'configuration','adapter':'claude','peer':'protocol_fixture','installed_vendor_ordering':'unverified'},{'event':'callbacks_held','ids':['A','B'],'activity':x['before']['activity']},{'event':'callback_released','id':'A','completed':x['completed_callbacks'],'pending':['B'] if x['b_still_held'] else [],'activity':x['after_a']['activity']}]
 b.add('F06-overlap','F06','product_defect',obs,'concurrent_activity','p6',[s],['H2','H7','H3','H8'],'Activity idle is reported while B is explicitly held. This schedule is demonstrated only with protocol peer.')
 b.add('F06-all-complete','F06','valid_control',[obs[0],{'event':'callbacks_released','ids':['A','B'],'pending':[],'activity':x['after_b_control']['activity']}],'legitimate','none',[s],['H2','H7'],'Idle after both completions is legitimate.')
 # The actual exception classes and executed counts are retained; verdict names and marker text are not packet inputs.
 cal='review/tracks/checker_calibration/calibrator-adversary/results.json'
 variants=[('intended-assertion','AssertionError','intended assertion',1,'legitimate','valid_control'),('unrelated-timeout','TimeoutError','helper body',1,'assertion_proxy','checker_defect'),('runtime-missing-dependency','ModuleNotFoundError','helper import',1,'assertion_proxy','checker_defect'),('collection-missing-dependency','ModuleNotFoundError','collection import',0,'infrastructure','environment_failure'),('skipped',None,'skip',1,'insufficient','incomplete'),('escaped',None,'target returns matching value',1,'legitimate','valid_control')]
 for i,(name,error,origin,targets,mechanism,kind) in enumerate(variants):
  x,s=b.source(cal,[i]);rawpath=f'review/tracks/checker_calibration/calibrator-adversary/{name}/mutant-run/command.json'
  obs=[{'event':'control_run','exit':x['control_exit']},{'event':'target_run','exit':x['mutant_exit'],'exception_type':error,'target_cases':x['work_calibrator']['targets'],'skips':x['work_calibrator']['skips'],'errors':x['work_calibrator']['errors'],'failures':x['work_calibrator']['failures'],'assertion_expression':'assert compute() == 1'},{'event':'calibrator_result','native':x['native_calibrator'],'work':x['work_calibrator']['classification']}]
  # Raw JUnit is retained independently of interpretive origin labels.
  xml=root/f'review/tracks/checker_calibration/calibrator-adversary/{name}/mutant.xml'
  if not xml.exists():
   options=list((root/f'review/tracks/checker_calibration/calibrator-adversary/{name}').rglob('*.xml'));xml=options[0] if options else None
  refs=[s]
  module_path=f'review/tracks/checker_calibration/calibrator-adversary/{name}/mutant/module.py'
  module=(root/module_path).read_text()
  b.sources[module_path]={'sha256':digest((root/module_path).read_bytes()),'bytes':(root/module_path).stat().st_size}
  module_key='s'+digest(module_path.encode())[:14];b.raw[module_key]={'source':module_path,'selector':[],'text':module};refs.append(module_key)
  obs[1]['helper_source']=module.replace('deliberately_missing_calibration_dependency','dependency_A')
  if xml:
   rel=str(xml.relative_to(root));b.sources[rel]={'sha256':digest(xml.read_bytes()),'bytes':xml.stat().st_size};key='s'+digest(rel.encode())[:14];b.raw[key]={'source':rel,'selector':[],'text':xml.read_text()};refs.append(key)
  b.add('F07-'+name,'F07',kind,obs,mechanism,'p4' if kind=='checker_defect' else ('insufficient' if kind=='incomplete' else 'none'),refs,['B4'],'Actual exception origin and execution counts distinguish target assertion from unrelated runtime/collection failures; intended assertion is a valid calibrator control.')
 faults='review/tracks/checker_calibration/evidence-faults/results.json'
 x,s=b.source(faults,['results',0]);m=x['mutation']
 native_path='review/tracks/checker_calibration/evidence-faults/native-arguments-mismatch-input.json'
 native_input=json.loads((root/native_path).read_text())
 native=next(o for o in native_input['raw_records']['observations'] if o['id']==m['observation_id'])
 related=[(i,o) for i,o in enumerate(native_input['raw_records']['observations']) if o['observation'].get('call_id')==native['observation']['call_id']]
 native_refs=[b.source(native_path,['raw_records','observations',i])[1] for i,o in related]
 obs=[{'event':'native_call','call_id':'call-A','action':'send_message','destination_actor':'actor-B'},{'event':'executed_callback','call_id':'call-A','action':'send_message','destination_actor':m['before']['destination']['actor']},{'event':'checker_run','passed_cases':x['passed_cases'],'failed_cases':len(x['failed_cases'])},{'event':'receipt_policy','verified':x['production_receipt_control']['verified'],'diagnostics':x['production_receipt_control']['diagnostics']}]
 b.add('F08-arguments','F08','checker_defect',obs,'evidence_contradiction','p3',[s,*native_refs],['B5','H8','H3','A2'],'Original native and application arguments differ on the joined call while checker passes. Input was substituted evidence, not a demonstrated live wrong dispatch.')
 control_path='review/tracks/checker_calibration/protocol-control/run.json'
 control_input=json.loads((root/control_path).read_text())
 control_refs=[b.source(control_path,['raw_records','observations',i])[1] for i,o in enumerate(control_input['raw_records']['observations']) if o['observation'].get('call_id')==native['observation']['call_id']]
 b.add('F08-agreement','F08','valid_control',[{'event':'native_call','call_id':'call-A','action':'send_message','destination_actor':m['before']['destination']['actor']},{'event':'executed_callback','call_id':'call-A','action':'send_message','destination_actor':m['before']['destination']['actor']},{'event':'checker_run','passed_cases':46,'failed_cases':0}],'legitimate','none',control_refs,['H3','B5'],'Unmodified raw native arguments agree with executed callback for the same call.')
 x,s=b.source(faults,['results',1]);a=x['mutation']['appended_attempt'];raw,s2=b.source('review/tracks/checker_calibration/evidence-faults/later-unknown-attempt-input.json',['raw_records','attempts']);same=[a0 for a0 in raw if a0['message_id']==a['message_id'] and a0['recipient']==a['recipient']]
 obs=[{'event':'attempt','message_id':q['message_id'],'recipient':q['recipient'],'sequence':q['sequence'],'status':q['status']} for q in sorted(same,key=lambda q:q['sequence'])]+[{'event':'checker_run','passed_cases':x['passed_cases'],'failed_cases':len(x['failed_cases'])},{'event':'receipt_policy','verified':x['production_receipt_control']['verified'],'diagnostics':x['production_receipt_control']['diagnostics']}]
 b.add('F09-attempts','F09','checker_defect',obs,['evidence_contradiction','post_effect_uncertainty'],'p3',[s,s2],['B5','H8','H7','A2'],'Later unknown attempt cannot be certified by an earlier successful attempt. Synthetic evidence substitution; no claim of normal retry producing this history.')
 baseline_attempts,br=b.source('review/tracks/checker_calibration/protocol-control/run.json',['raw_records','attempts'])
 initial=[q for q in baseline_attempts if q['message_id']==a['message_id'] and q['recipient']==a['recipient']]
 b.add('F09-original-attempt','F09','valid_control',[*({'event':'attempt','message_id':q['message_id'],'recipient':q['recipient'],'sequence':q['sequence'],'status':q['status']} for q in sorted(initial,key=lambda q:q['sequence'])),{'event':'checker_run','passed_cases':46,'failed_cases':0}],'legitimate','none',[br],['B5','H7'],'Original unmodified protocol control has a submitted attempt without an appended later unknown.')
 x,s=b.source(faults,['results',2])
 timing_path='review/tracks/checker_calibration/evidence-faults/reply-after-work-completion-input.json'
 timing_input=json.loads((root/timing_path).read_text())
 timing_refs=[];chronology=[]
 for i,e in enumerate(timing_input['provider_events']):
  if e['event']=='work_completed' and e['peer']=='claude':
   _,ref=b.source(timing_path,['provider_events',i]);timing_refs.append(ref);chronology.append({'event':'work_completed','at':e['at'],'actor':'claude'})
 for i,e in enumerate(timing_input['raw_records']['messages']):
  if e['body'].get('verification',{}).get('case_id')=='work-codex-claude-0':
   _,ref=b.source(timing_path,['raw_records','messages',i]);timing_refs.append(ref);chronology.append({'event':e['body']['verification']['kind'],'at':e['created_at'],'actor':e['created_by'],'case':'exchange-A'})
 chronology.sort(key=lambda e:e['at'])
 obs=[{'event':'requirement','reply_must_precede_work_completion':True},{'event':'time_shift','subject':'work_completed','after':x['mutation']['after'],'before':x['mutation']['before']},{'event':'timing_observation','case':'exchange-A',**next(iter(x['timing'].values()))},{'event':'checker_run','passed_cases':x['passed_cases'],'failed_cases':len(x['failed_cases'])}]
 b.add('F10-timing','F10','checker_defect',[obs[0],*chronology,*obs[1:]],'assertion_proxy','p3',[s,*timing_refs],['H2','H8','B5'],'Checker records required reply-before-completion false but passes all cases; submission timing is insufficient.')
 # One shared valid raw-run control covers all three checker families, kept heldout.
 ctl,s=b.source('review/tracks/checker_calibration/evidence-faults/control.json')
 timing=[c for c in ctl['cases'] if c['id']=='work-codex-claude-0'][0]['evidence']['timing']
 b.add('F10-timing-control','F10','valid_control',[{'event':'requirement','reply_must_precede_work_completion':True},{'event':'timing_observation','case':'exchange-A',**timing},{'event':'checker_run','passed_cases':sum(c['status']=='passed' for c in ctl['cases']),'failed_cases':0}],'legitimate','none',[s],['H2','H8','B5'],'Original gated schedule satisfies stronger reply timing.')
 x,s=b.source(faults,['results',4])
 b.add('F08-identity-control','F08','valid_control',[{'event':'native_binding','session_native_id':'native-A','call_native_id':'native-B'},{'event':'checker_run','passed_cases':x['passed_cases'],'failed_cases':len(x['failed_cases']),'diagnostics':[c['assertion'] for c in x['failed_cases']]}],'legitimate','none',[s],['H3','B5'],'Checker rejects the mismatched native identity; this is a correctly rejected invalid input control.')
 x,s=b.source(faults,['results',3])
 b.add('F08-missing-observation','F08','incomplete',[{'event':'coverage','requested_cases':46,'executed_cases':45},{'event':'checker_run','passed_cases':x['passed_cases'],'missing_case_status':x['failed_cases'][0]['status']}],'insufficient','insufficient',[s],['B5'],'Missing coverage cannot count as behavioral success or target detection.')
 if additional and additional.exists():
  add=json.loads(additional.read_text())
  for x in add['cases']:
   answer=x['answer'];answer['kind']={'valid':'valid_control','environment':'environment_failure'}.get(answer['kind'],answer['kind']);ident=x['id'];packet=x['packet'];packet['probe_options']=PROBES
   b.cases.append({'id':ident,'packet':packet});b.answers[ident]=answer;b.map[ident]={'independent_variant':True,'definition_source':str(additional.name),'packet_sha256':digest(canonical(packet)),'candidate':CANDIDATE,'source':x.get('source'),'extraction':x.get('extraction')};b.ids[ident]=ident
 # Relations are independent key judgments, including explicit ambiguous same-mechanism claims.
 relations=[('F04-codex','F04-grok','shared_invariant','development','Shared currentness invariant with separate adapter cache implementations.'),('F04-codex','F05-0','shared_invariant','heldout','Same currentness invariant, adapter replay cache versus assessment dependency identity.'),('F02-1','F03-1','similar_symptom','heldout','Both triggered around replacement, but authority rebinding and post-effect certainty are distinct.'),('F08-arguments','F09-attempts','shared_invariant','heldout','Both lose evidence sufficiency, but contradiction overwrite versus attempt ordering.'),('F01-memory','F01-sqlite','same_mechanism','development','Same delivery snapshot projection across storage implementations.'),('F01-memory','F06-overlap','no_relationship','heldout','Read scope and aggregate activity provide no shared implementation or violated invariant.'),('F07-unrelated-timeout','F07-runtime-missing-dependency','same_mechanism','development','Same marker-in-traceback classifier with different helper failures.'),('F08-missing-observation','F08-arguments','insufficient','heldout','Missing required coverage supplies no observation of argument checking.')]
 pairs=[];answers={}
 for i,(left,right,rel,split,why) in enumerate(relations):
  ident='r'+digest((left+right).encode())[:10];pairs.append({'id':ident,'left':b.ids[left],'right':b.ids[right]});answers[ident]={'relationship':([rel,'insufficient'] if rel=='same_mechanism' else (['similar_symptom','shared_invariant','no_relationship','insufficient'] if left=='F02-1' and right=='F03-1' else [rel])),'family':b.answers[b.ids[left]]['family']+'+'+b.answers[b.ids[right]]['family'],'split':split,'rationale':why}
 b.cases.sort(key=lambda x:x['id'])
 dump(out/'cases.json',{'version':VERSION,'cases':b.cases})
 dump(out/'answer_key.json',{'version':VERSION,'frozen_before_live':True,'cases':b.answers,'relationships':answers})
 dump(out/'pairs.json',{'version':VERSION,'pairs':pairs})
 dump(out/'provenance.json',{'version':VERSION,'candidate':CANDIDATE,'archive_integrity':integrity,'sources':b.sources,'cases':b.map,'neutral_value_replacements':{'REVIEW_SYNTHETIC_SECRET_81c6df':'marker-A','known_defect':'condition-A','wrong-recipient-canary':'actor-B','deliberately_missing_calibration_dependency':'dependency_A'},'limits':['Selected packets are compact human-designed extracts, not a blind automated discovery benchmark.','Control cases and adapter/schedule reruns are clustered by family; case counts are not independent families.','F06 installed-vendor reachability is unverified.','F08-F10 are evidence-substitution checker experiments, not live wrong delivery claims.']})
 dump(out/'raw_observations.json',{'version':VERSION,'records':b.raw})
 dump(out/'splits.json',{'version':VERSION,'declared_before_baseline':True,'development_families':sorted(DEVELOPMENT),'heldout_families':['F03','F05','F06','F08','F09','F10'],'independent_variants':'F07 development transfer, not unseen-family performance','case_splits':{k:v['split'] for k,v in b.answers.items()}})
 dump(out/'freeze.json',{'version':VERSION,'files':{p.name:digest(p.read_bytes()) for p in sorted(out.glob('*.json')) if p.name!='freeze.json'},'case_count':len(b.cases),'relationship_count':len(pairs)})
 return {'cases':len(b.cases),'pairs':len(pairs),'integrity':integrity}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--out',type=Path,default=Path(__file__).parent/'data');p.add_argument('--additional',type=Path);a=p.parse_args();print(json.dumps(build(a.evidence,a.out,a.additional),indent=2))
