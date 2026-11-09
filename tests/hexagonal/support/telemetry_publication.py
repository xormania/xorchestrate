"""Verified fixture bundles and an in-memory destination for publication contracts."""

import hashlib
import json
from pathlib import Path

from tests.hexagonal.support import telemetry_preservation as preservation

LINEAGE = {"dataset_id": "fixture-dataset", "collector_id": "fixture-collector"}
snapshot_api = preservation.snapshot_api


def verified_bundle(root):
    """Create a real bundle through the preservation snapshot path."""
    root = Path(root)
    dataset = preservation.Dataset(root / "live")
    try:
        dataset.append("published", {"answer": "retained"}, content=b"published artifact\n")
        bundle = root / "bundle"
        manifest = snapshot_api().create_snapshot(dataset.database, dataset.artifacts, bundle)
    finally:
        dataset.close()
    return bundle, manifest


def tamper(bundle, member):
    """Change one bundle member after creation so verification must refuse it."""
    bundle = Path(bundle)
    if member == "database":
        with (bundle / "telemetry.sqlite3").open("ab") as stream:
            stream.write(b"changed after snapshot")
    elif member == "manifest":
        manifest = preservation.read_manifest(bundle)
        manifest["source"]["event_count"] += 1
        preservation.write_manifest(bundle, manifest)
    else:
        raise ValueError(f"unknown bundle member {member!r}")


class MemoryPublisher:
    """A destination holding published bytes in memory.

    ``lookup`` verifies the stored database bytes against the stored manifest and
    the requested identity and lineage. ``lose_acknowledgment`` stores the bytes
    and then fails the call, as a destination whose response never arrives.
    ``fail`` raises before any bytes are stored, from ``lookup`` or ``publish``.
    ``receipt_changes`` replaces fields of every receipt for a stored bundle.
    """

    def __init__(self, *, fail=None, lose_acknowledgment=False, receipt_changes=None):
        if fail not in (None, "lookup", "publish"):
            raise ValueError(f"unknown publisher failure {fail!r}")
        self.fail = fail
        self.lose_acknowledgment = lose_acknowledgment
        self.receipt_changes = dict(receipt_changes or {})
        self.stored = {}
        self.lookups = []
        self.publications = []

    @staticmethod
    def destination(bundle_id):
        return f"memory://bundles/{bundle_id}"

    def contacts(self):
        return len(self.lookups) + len(self.publications)

    def lookup(self, bundle_id, *, expected_lineage):
        self.lookups.append({"bundle_id": bundle_id, "expected_lineage": dict(expected_lineage)})
        if self.fail == "lookup":
            raise OSError("private destination exception text")
        stored = self.stored.get(bundle_id)
        if stored is None:
            return None
        manifest = json.loads(stored["files"]["manifest.json"])
        database = stored["files"].get(manifest["database"]["path"], b"")
        verified = (manifest["bundle_id"] == bundle_id
                    and hashlib.sha256(database).hexdigest() == manifest["database"]["sha256"]
                    and stored["lineage"] == dict(expected_lineage))
        receipt = {"verified": verified, "bundle_id": manifest["bundle_id"],
                   "lineage": dict(stored["lineage"]), "destination": self.destination(bundle_id)}
        receipt.update(self.receipt_changes)
        return receipt

    def publish(self, bundle, *, expected_lineage):
        bundle = Path(bundle)
        self.publications.append({"bundle": bundle, "expected_lineage": dict(expected_lineage)})
        if self.fail == "publish":
            raise OSError("private destination exception text")
        files = {str(path.relative_to(bundle)): path.read_bytes()
                 for path in sorted(bundle.rglob("*")) if path.is_file()}
        bundle_id = json.loads(files["manifest.json"])["bundle_id"]
        self.stored[bundle_id] = {"files": files, "lineage": dict(expected_lineage)}
        if self.lose_acknowledgment:
            raise TimeoutError("private destination exception text")
