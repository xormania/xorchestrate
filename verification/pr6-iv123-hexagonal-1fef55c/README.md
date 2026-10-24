# PR #6 repair handoff and checksummed evidence

[Consolidated handoff](Handoff.md) · [Replay commands](REPLAY.md) · [Normal CI results](CI-RESULTS.md) · [Identities](identities.json) · [Closure matrix](closure-matrix.json)

Product commit: `1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`. The commit containing this directory is the separate evidence publication, not the measured product. PR #6 remains unmerged.

Download every file in this directory, preserving names. The parts form one ZIP; do not unzip individual parts.

```sh
sha256sum -c SHA256SUMS
python3 restore_review.py --package . --out /fresh/path/pr6-evidence
```

The helper checks every part, the combined ZIP, its embedded complete manifest, inventory, and every restored regular file. It refuses an existing destination and unsafe archive paths. Recorded symlinks are not followed or recreated; transient IPC files are described in MANIFEST.json. They are not evidence payload bytes.

The full archive is `pr6-iv123-evidence.zip` (305525395 bytes), SHA-256 `fcea4c755438f12f4d5d865d9b2bcf526bb3c532adf882e63ba38dc0895462c9`, with 72508 regular payload files. Product commit: `1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`; product tree: `75ecb3ef4edbc381012fe74e416a9abd3fb5c667`. This evidence publication has its own Git commit.

After restoration, consult the consolidated handoff and replay instructions in the publication. The source tar under `evidence/packaging/` preserves the exact candidate source and executable modes; normal independent verification should still fetch the specified commit directly from GitHub.

The archive retains failed and superseded attempts as well as final evidence. Fresh restoration verifies all 72,508 payload files; restoration and replay QA are retained under `QA/`. The final archive is split into seven files so each remains below 45 MiB.
