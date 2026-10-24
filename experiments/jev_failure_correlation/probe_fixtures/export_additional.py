"""Fixed extraction of independent development-transfer observations, with provenance."""
import hashlib
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
raw_path = ROOT / 'evidence/independent_generation/p4/observations.json'
raw = json.loads(raw_path.read_text())
by_id = {r['variant']:r for r in raw['records']}
fixtures = {v['id']:v for v in json.loads((ROOT/'probe_fixtures/calibration.json').read_text())['variants']}
cases = []
for case_id, variant, mechanism, kind, behavior in [
 ('v01','q03','assertion_proxy','checker_defect','The helper imports read_value; read_value raises ValueError before returning to the comparison.'),
 ('v02','q04','assertion_proxy','checker_defect','The helper imports read_value; read_value raises its own AssertionError before returning to the comparison.'),
 ('v03','q05','legitimate','valid','The helper imports read_value; read_value returns 1 and the comparison passes.'),
 ('v04','q06','infrastructure','environment','Importing the module raises ModuleNotFoundError before any test function executes.')]:
 r = by_id[variant]
 target = r['target_observation']
 diagnostic = target['diagnostic'].splitlines()[0] if target and target['diagnostic'] else None
 if variant=='q06': diagnostic='ModuleNotFoundError'
 packet = {'observations':[
  {'configuration':{'entry':'pytest → test function → compute → dependency function','comparison':'returned number equals 1','test_control':'separate unrelated test'},
   'source_behavior':behavior,
   'source': {'entry':fixtures[variant]['module'],'dependency':fixtures[variant]['dependency']},
   'control_exit':r['control_exit'],'mutated_execution_exit':r['exit_code'],
   'target_status':target['status'] if target else 'not observed',
   'terminal_exception':diagnostic,
   'classifier_outputs':{'native':r['native_classification'],'work':r['work_classification']}}]}
 cases.append({'id':case_id,'packet':packet,'answer':{'family':'F07','split':'development','kind':kind,
    'mechanism':[mechanism],'probe':['p4'] if kind=='checker_defect' else ['none','p4'],
    'rationale':behavior+' Compare executed outcome with both recorded classifier decisions.',
    'scope':'independent dependency/exception transfer within known F07 family, not unseen-family evaluation'},
    'source':{'raw_path':str(raw_path.relative_to(ROOT)),'raw_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),'variant':variant,
              'fixture':'probe_fixtures/calibration.json'},
    'extraction':{'method':'select recorded exit/status/classifier decisions; first exception line; source_behavior derived from retained fixture source',
       'omitted':['full traceback assertion marker and source filenames','absolute execution paths','precomputed expected/reproduced/credit fields']}})
(ROOT/'additional_cases.json').write_text(json.dumps({'version':'1','cases':cases},indent=2)+'\n')
