"""Public bundle receipts stay bounded while saved evidence remains complete."""

import hashlib
import json


def complete_manifest_contract(subject, path):
    manifest, content = subject.saved_manifest(path)
    unresolved = manifest["source"]["unresolved_links"]
    assert len(unresolved) == 1200, "BUNDLE_SAVED_MANIFEST_MUST_RETAIN_COMPLETE_LINK_CLOSURE"
    assert [row["target_id"] for row in unresolved] == subject.link_targets
    assert all(row["event_id"] == "manifest-stress-trial" and row["relation"] == "references"
               for row in unresolved)
    assert len(content) > 1048576, "The independent fixture must exceed a normal MCP frame"
    return manifest, content


def compact_receipt_contract(subject, receipt, path):
    assert len(json.dumps(receipt).encode("utf-8")) <= 65536, \
        "PUBLIC_BUNDLE_RECEIPT_MUST_BE_BOUNDED_WITH_COMPLETE_MANIFEST_RETAINED_SEPARATELY"
    manifest, content = complete_manifest_contract(subject, path)
    assert receipt["status"] == manifest["status"] == "complete"
    assert receipt["format"] == manifest["format"]
    assert receipt["bundle_id"] == manifest["bundle_id"]
    assert receipt["completeness"] == manifest["completeness"]
    assert receipt["source_cutoff"] == manifest["source"]["cutoff"]
    assert receipt["event_count"] == manifest["source"]["event_count"]
    assert receipt["artifact_count"] == len(manifest["artifacts"])
    assert receipt["unavailable_artifact_count"] == len(manifest["unavailable_artifacts"])
    assert receipt["unresolved_link_count"] == len(manifest["source"]["unresolved_links"])
    reference = receipt["manifest_reference"]
    assert reference["path"] == subject.manifest_path(path)
    assert reference["sha256"] == hashlib.sha256(content).hexdigest(), \
        "PUBLIC_BUNDLE_RECEIPT_MUST_REFERENCE_EXACT_SAVED_MANIFEST_BYTES"
    assert reference["size_bytes"] == len(content)
    assert reference["media_type"] == "application/json"
    return reference


def public_operation_receipt_contract(subject, operation):
    subject.seed_links()
    receipt, path = subject.public_operation(operation)
    reference = compact_receipt_contract(subject, receipt, path)
    verified = subject.verify(path)
    assert compact_receipt_contract(subject, verified, path) == reference, \
        "PUBLIC_BUNDLE_VERIFY_MUST_RETURN_THE_SAME_STABLE_MANIFEST_REFERENCE"
    if operation == "telemetry_restore":
        original, original_bytes = subject.saved_manifest(subject.source_bundle)
        restored, restored_bytes = subject.saved_manifest(path)
        assert restored == original and restored_bytes == original_bytes


def mcp_receipt_contract(subject):
    subject.seed_links()
    subject.public_operation("telemetry_snapshot")
    receipt, wire_frame = subject.mcp_verify()
    assert len(wire_frame) <= 1048576, "PUBLIC_MCP_BUNDLE_RECEIPT_MUST_FIT_DEFAULT_RESPONSE_FRAME"
    compact_receipt_contract(subject, receipt, subject.source_bundle)
