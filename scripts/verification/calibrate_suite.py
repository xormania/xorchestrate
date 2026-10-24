"""Repeat the reviewed native/delivery critical faults on one exact candidate."""
import argparse
import json
from pathlib import Path

from .__main__ import calibrate
from .capture import candidate_identity, now, verify_candidate, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=Path.cwd())
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, default=Path(__file__).with_name('faults.json'))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    candidate = candidate_identity(args.candidate)
    results = []
    for case in json.loads(args.inventory.read_text()):
        try:
            result = calibrate(args.candidate, args.out / case['name'], case['mutation'], case['selectors'],
                               case['target'], case['diagnostic'], case['unaffected'])
        except Exception as error:
            result = {'classification': 'setup_error', 'error': str(error)}
        results.append({'name': case['name'], **result})
    integrity = verify_candidate(args.candidate, candidate)
    report = {'candidate': candidate, 'results': results, 'finished_at': now(), 'candidate_integrity': integrity,
              'passed': integrity['passed'] and all(row['classification'] == 'detected' for row in results)}
    write_json(args.out / 'suite.json', report)
    print(json.dumps({'passed': report['passed'], 'results': [(r['name'], r['classification']) for r in results]}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
