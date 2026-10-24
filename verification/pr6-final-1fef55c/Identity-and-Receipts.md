# Bounded independent IV03 and product receipt assessment

**No material blocker demonstrated.** This assessment is limited to product `1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`, tree `75ecb3ef4edbc381012fe74e416a9abd3fb5c667`. The disposable identity worktree remained clean; before/after tracked byte manifests are identical. No product implementation was changed.

The main review owns the original/closure replays, full suite and 47-case published receipt probe. This track inspected the changed identity predicates and added 30 finite requirement-derived cases. All 30 matched their independently declared outcomes. Raw controlled inputs, complete reports and actual diagnostics are in `probe-output/`; expectations do not call the checker or receipt policy. Candidate fixture adapters only construct data and invoke the subject. These are new controlled evidence checks, not claims of fresh live dispatch or authenticated vendor acceptance.

## Required outcomes

Both the public checker and independently implemented product receipt policy reject jointly wrong native/requested/completed/returned actors even when those records agree with one another: the independent source session remains authoritative. They reject a null requested actor alongside an absent optional native actor, split application/native session and incarnation claims on returns, a requested claim left at original work after coherent alternate-work selection, explicit null returned work, and a contradictory duplicated typed return. They preserve genuinely absent optional actor claims, genuinely absent native-return arguments, coherent alternate selected work and qualified historical records.

Lifecycle cases reject null operation-return incarnation, contradictory durable actor despite absent native actor, a null selected-work claim on an additional requested observation, and null returned work after coherent alternate selection. Pristine and coherent alternate lifecycle controls pass. These expectations follow literal controlled actors/sessions/work; they are not inferred from subject verdict logic.

## Alternate-work adaptation

The correction is justified. `audit-adaptation.py` uses only the standard library and confirms the repair's retained original IV03 script is byte-identical to the independently restored original publication. It reconstructs the original positive-control mutation and enumerates all applicable raw selected-work claims for Codex, Claude and Grok. The original mutation changed native/completed arguments and saved effect but left a requested claim, and where present typed native-return claims, at the original work. Thus it was not a coherent alternate-work control under the stated requirement.

The adapted historical input contains only `explicit-alternate-work` selected-work claims while retaining original outer observation/source work and session identity. The paired requested-only contradiction remains inconsistent. No rejection case was deleted, inverted or waived. `adaptation-audit.json` retains record IDs, paths and values for original, adapted and paired-negative inputs. This is independent raw evidence analysis; it does not substitute historical inputs for the main review's fresh protocol execution.

## Investigations adjudicated without blockers

An optional nested `observation.session_id` is a recognized typed claim, as shown by `core.identity_matches`, `exchange._valid_observation` and `test_callback_identity.py`. When a row's outer application session and native incarnation are coherently historical, both subjects exclude it from current proof even if its optional nested session field is changed to current or null. The exploratory matrix retains 24 historical cases without treating the inconsistent cases as predetermined required rejections.

The fresh complete composed run confirms this boundary: baseline and coherent history pass 46/46; changing only the historical copy's nested session to current still passes 46/46 and product receipt. Conversely, a directly applicable current native observation with contradictory nested session fails the checker (44/46, incomplete) and product receipt. The separate adjudicator removed necessary current native/requested/completed proof: all six checker/receipt controls rejected despite the nested-current historical row. Therefore no current proof borrows authority from that excluded row; the complete valid current chain remains independently present. This is a historical diagnostic limitation, not a demonstrated material identity-credit failure or required evidence gap. See `fresh-history-output/` and the main handoff's `ci-identity/nested-session-controls.json`.

A separate exploratory call observed that exchange-only criteria can accept a conflicting extra requested callback when the mandatory native-call audit case is deliberately omitted. Default complete criteria reject through that audit. `docs/behavioral-verification.md` explicitly assigns every requested/completed callback to the native-call audit, and the required complete inventory includes it. Removing the mandated case does not establish a supported false acceptance. This is retained in `exploratory-notes.txt`, with no recommendation to broaden this review.

## Reproduction

Use the final pinned product checkout and Python 3.12.14 with the main review's environment:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" probe.py --candidate "$CANDIDATE" --out /fresh/probe-output
"$PYTHON" audit-adaptation.py --evidence "$RESTORED/evidence" --original "$ORIGINAL_RESTORED" --out /fresh/adaptation-audit.json
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" fresh-history.py --candidate "$CANDIDATE" --input "$FRESH/composed/protocol" --out /fresh/history-output
```

No check in this assigned track remains blocked or unexecuted. The original and adapted publication evidence is referenced rather than copied wholesale. There is no demonstrated live wrong-principal dispatch and no authenticated vendor acceptance claim. The broader migration, IV04 encoding and existing synchronization follow-ups remain outside this track.
