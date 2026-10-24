# PR #6 independent verification handoff

Use these files directly from GitHub; ChatGPT attachment downloads are unnecessary.

- [Independent verification assignment](Xorchestrate-PR6-Independent-Verification.md)
- Full repair evidence ZIP, stored as four binary parts: [1](Xorchestrate-Repair-Evidence.zip.part-01), [2](Xorchestrate-Repair-Evidence.zip.part-02), [3](Xorchestrate-Repair-Evidence.zip.part-03), [4](Xorchestrate-Repair-Evidence.zip.part-04)
- [File checksums](SHA256SUMS)
- [Reconstructed ZIP checksum](ARCHIVE.sha256)

The reconstructed ZIP contains the repair report, original independent review and owner brief, raw measurements, manifests, recovery helpers, and replay instructions. Splitting accommodates the publication interface's request limit; concatenation restores the original bytes unchanged.

## Retrieve and begin

Read the assignment in full and execute it in a fresh independent session. To retrieve the files through Git:

```sh
git clone --single-branch --branch handoff/pr6-independent-verification https://github.com/xormania/xorchestrate.git xorchestrate-pr6-handoff
cd xorchestrate-pr6-handoff/handoffs/pr6-independent-verification
sha256sum --check SHA256SUMS
cat Xorchestrate-Repair-Evidence.zip.part-01 Xorchestrate-Repair-Evidence.zip.part-02 Xorchestrate-Repair-Evidence.zip.part-03 Xorchestrate-Repair-Evidence.zip.part-04 > Xorchestrate-Repair-Evidence.zip
sha256sum --check ARCHIVE.sha256
```

This branch carries the handoff files. **It is not the product candidate to test.** Follow the assignment and ZIP replay guide to create a separate product checkout at:

```text
ef7567516520543970af55b6f19534cb66eda357
```

Expected product tree: `a0813aae760e6e5bb6245693bd92b7e7c321348b`.

PR: https://github.com/xormania/xorchestrate/pull/6

Evidence publication: https://github.com/xormania/xorchestrate/tree/663f0634f59c5d6f6c54910c8f995a6af9e6a490/docs/repair/evidence

The ZIP includes a historical `source.bundle` that lacks the final candidate. Use the documented GitHub clone/fetch route and verify the exact commit/tree. Do not mistake the bundle's superseded repair branch for the final subject.

## Launch prompt

Read `Xorchestrate-PR6-Independent-Verification.md` in full, then execute its assignment using `Xorchestrate-Repair-Evidence.zip`. Independently verify PR #6 at `ef7567516520543970af55b6f19534cb66eda357`. Adversarially challenge F01–F10, the later aggregate-completion repair, and the checker/calibration machinery. Reproduce material claims, retain controls and raw evidence, and deliver the verification report, per-finding closure verdicts, replayable evidence ZIP, and a scoped integration recommendation. Keep authenticated vendor acceptance identified as xor's planned local work. Do not repair the product, modify the Jev experiment, or merge. Execute the verification; do not stop at a plan.

## Preservation

Repair ZIP SHA-256: `27db9e669dc136c866a37b2f4164471f8ce2bd581532225217711ac062df69fe`.

Publishing this handoff does not establish repair closure. The independent verification remains the task to perform. Current explicit instructions from xor govern over historical documents.
