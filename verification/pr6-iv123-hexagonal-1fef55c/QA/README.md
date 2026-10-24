# Publication restoration QA

The complete frozen archive restored successfully, with every regular payload checked against the embedded and published manifest. All publication parts and metadata pass SHA256SUMS. The restored source tar matches all 1,339 Git blob IDs at the exact candidate, including regular executable modes and link types. The portable native-result replay was executed from its restored path with restored historical raw inputs and the clean final candidate: two valid controls and six contradictory claims have their intended outcomes.

A supplemental source audit initially compared a tar symlink's permission bits with Git's symlink type mode, causing an audit-helper assertion. The original script (`verify_source_archive_v2.py`) and failure log remain. The corrected script checks link type/content and regular-file executable modes; its v3 result passes. No archive bytes, evidence payloads or product code changed to address that audit-helper error.

These QA files were generated after the evidence freeze and are kept outside the archived payload. `QA-SUMMARY.json` records the final package identities and results; `commands.json` records replay commands. `SHA256SUMS` covers every other regular QA file.
