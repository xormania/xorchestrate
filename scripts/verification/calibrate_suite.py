"""Run registered fault inventories on one exact candidate.

Selects inventories by name or by stage (default: those registered at ci),
runs each case through the calibration service with the isolated trial
adapter, and writes one suite report. Exits 2 for an unknown or unregistered
inventory before any case runs.
"""
import argparse
import json
import sys
from pathlib import Path

from scripts.testkit.adapters.evidence import candidate_identity, now, verify_candidate, write_json

from .__main__ import run_trial
from .fault_calibration import calibrate_inventory
from .inventory_files import InventoryError, load_inventory, load_registry, select

INVENTORIES = Path(__file__).with_name("inventories")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry-dir", type=Path, default=INVENTORIES)
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--inventory", action="append", default=[], help="registered inventory name; repeatable")
    which.add_argument("--stage", choices=("ci", "pre-draft", "manual"))
    args = parser.parse_args(argv)
    root, output = args.candidate.resolve(), args.out.resolve()
    if output == root or root in output.parents:
        parser.error("--out must be outside the candidate tree")
    try:
        names = select(load_registry(args.registry_dir), names=args.inventory, stage=args.stage)
        inventories = [load_inventory(args.registry_dir, name) for name in names]
    except InventoryError as error:
        print(error.code, file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=False)
    candidate = candidate_identity(root)
    reports = {}
    for inventory in inventories:
        events = output / (inventory.name + ".events.jsonl")

        def sink(event, events=events):
            with events.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")

        reports[inventory.name] = calibrate_inventory(
            inventory, trial=run_trial(root, output / inventory.name), sink=sink,
            candidate_sha256=candidate["content_sha256"])
    integrity = verify_candidate(root, candidate)
    passed = bool(reports) and integrity["passed"] and all(report["passed"] for report in reports.values())
    write_json(output / "suite.json", {"candidate": candidate, "candidate_integrity": integrity,
                                       "inventories": reports, "passed": passed, "finished_at": now()})
    print(json.dumps({"passed": passed, "results": {
        name: [(row["name"], row["classification"]) for row in report["results"]]
        for name, report in reports.items()}}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
