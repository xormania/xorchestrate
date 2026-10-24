"""Replay frozen selections with an explicit pytest fixture root (probe harness v1.2).

The measured v1.1 probe module and original hosted outputs remain unchanged.
Only pytest root discovery is overridden; fixture source, assertions, classifiers,
selection budget and candidate are identical. No model calls occur here.
"""
from pathlib import Path
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from experiments.jev_failure_correlation import probes

original_run = probes.subprocess.run

def fixture_root_run(command, *args, **kwargs):
    if isinstance(command, list) and command[1:4] == ['-m', 'pytest', '-q']:
        command.append('--rootdir=' + str(Path(kwargs['cwd']).resolve()))
    return original_run(command, *args, **kwargs)

if __name__ == '__main__':
    probes.subprocess.run = fixture_root_run
    status = probes.main()
    out = Path(sys.argv[sys.argv.index('--out') + 1])
    predictions = Path(sys.argv[sys.argv.index('--predictions') + 1])
    record = {
        'probe_harness_version': '1.2.0',
        'change': 'Explicit per-fixture pytest --rootdir; all other arguments and fixtures unchanged',
        'model_calls': 0,
        'replay_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'original_probe_sha256': hashlib.sha256(Path(probes.__file__).read_bytes()).hexdigest(),
        'predictions_sha256': hashlib.sha256(predictions.read_bytes()).hexdigest(),
        'execution_location': 'Codex Cloud; corrected replay of actual GitHub Actions selections',
    }
    (out / 'replay-binding.json').write_text(json.dumps(record, indent=2) + '\n')
    raise SystemExit(status)
