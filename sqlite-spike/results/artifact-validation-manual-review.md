# Artifact validation — manual review dossier

**Status:** review proposal only. No canonical write has been made. Filesystem reachability
below is diagnostic only; it must not decide validity. `artifact_link_id` is the proposed
stable relation subject: `artifact:<record_id>:<relation>:<uri>`.

| # | record / link | workspace | title / relation / URI | status / time | baseline (historical / contract) | evidence and diagnostic | suggested state / reason / confidence | human confirmation |
|---:|---|---|---|---|---|---|---|---|
| 1 | rec-004 / `artifact:rec-004:touches:repo://ai-pos/README.md` | abap-object-exporter | touches README.md | accepted / 10:18:16.795631Z | included / returned | canonical payload relation; no validation rows; URI structurally valid; checkout inaccessible | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 2 | rec-008 / `artifact:rec-008:touches:repo://ai-pos/README.md` | smart-timesheet | touches README.md | accepted / 10:18:16.795769Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 3 | rec-012 / `artifact:rec-012:touches:repo://ai-pos/README.md` | ai-pos-app | touches README.md | accepted / 10:18:16.795894Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 4 | rec-016 / `artifact:rec-016:touches:repo://ai-pos/README.md` | ai-pos | touches README.md | accepted / 10:18:16.796007Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 5 | rec-020 / `artifact:rec-020:touches:repo://ai-pos/README.md` | personal | touches README.md | accepted / 10:18:16.796116Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 6 | rec-024 / `artifact:rec-024:touches:repo://ai-pos/README.md` | abap-object-exporter | touches README.md | accepted / 10:18:16.796223Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 7 | rec-028 / `artifact:rec-028:touches:repo://ai-pos/README.md` | smart-timesheet | touches README.md | accepted / 10:18:16.796329Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 8 | rec-032 / `artifact:rec-032:touches:repo://ai-pos/README.md` | ai-pos-app | touches README.md | accepted / 10:18:16.796437Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 9 | rec-036 / `artifact:rec-036:touches:repo://ai-pos/README.md` | ai-pos | touches README.md | accepted / 10:18:16.796544Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 10 | rec-040 / `artifact:rec-040:touches:repo://ai-pos/README.md` | personal | touches README.md | accepted / 10:18:16.796649Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 11 | rec-044 / `artifact:rec-044:touches:repo://ai-pos/README.md` | abap-object-exporter | touches README.md | accepted / 10:18:16.796757Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 12 | rec-048 / `artifact:rec-048:touches:repo://ai-pos/missing-file.ts` | smart-timesheet | touches missing-file.ts | accepted / 10:18:16.796872Z | excluded / absent | fixture says `invalid`; no canonical validation evidence; URI structural form valid; checkout inaccessible | unknown / other / low | Choose one of the three options below. |
| 13 | rec-052 / `artifact:rec-052:touches:repo://ai-pos/README.md` | ai-pos-app | touches README.md | accepted / 10:18:16.797005Z | included / returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |
| 14 | rec-056 / `artifact:rec-056:touches:repo://ai-pos/README.md` | sap-work | touches README.md | accepted / 10:18:16.797128Z | included / not returned | same; no canonical validation evidence | unknown / migrated_from_baseline_review / low | Confirm intended target and relation ownership. |

All entries have `invalid_at: null`, `artifact_links` rows: `[]`, and final human fields
`decision: null`, `approved_by: null`, `approved_at: null`. “Returned” means present in the
contract-baseline query evidence; it is not a validity judgement.

## Read-only Git diagnostic enrichment

`repo://ai-pos/...` resolves unambiguously to the local Git root
`/Users/pavol/Documents/Personal/Projects/ai-pos`. For the 13 `README.md` relations, the path
exists, is tracked, clean, and last changed by `51cd243e50ed924f776e09a297016ee9642cfd4d`.
There is no deletion or rename evidence. Every record is nevertheless synthetic fixture input,
with the same `touches` relation to `rec-001`; this makes the relation semantics reviewable,
not automatically valid. `rec-048` is missing, untracked, absent from all Git history, and has
no evidenced replacement path. See `artifact-validation-review-evidence.json` for per-record
duplicate IDs and Git evidence. These diagnostics do not modify any proposed state.

## rec-048 — mandatory individual review

- **record_id:** `rec-048`
- **URI:** `repo://ai-pos/missing-file.ts`
- **fixture evidence:** `expected.artifact_validation="invalid"`
- **baseline behavior:** excluded
- **canonical evidence:** missing
- **reachability:** inaccessible / not testable on current mini-core
- **automatic state:** forbidden
- **requires_manual_review:** true

Choose exactly one only with supporting human evidence:

1. `verified_inactive / wrong_target` — only if `missing-file.ts` was intentionally a wrong
   fixture artifact.
2. `verified_inactive / intentionally_retired` — only if the relation was valid historically
   but should no longer be current.
3. `verified_active / manual_verified` — if the relation is intentional and current
   unavailability is environmental.

Without that decision, `unknown` is the safe default.

## Approval template

```yaml
ARTIFACT VALIDATION APPROVAL

rec-004:
  state:
  reason_code:
  note:
rec-008:
  state:
  reason_code:
  note:
rec-012:
  state:
  reason_code:
  note:
rec-016:
  state:
  reason_code:
  note:
rec-020:
  state:
  reason_code:
  note:
rec-024:
  state:
  reason_code:
  note:
rec-028:
  state:
  reason_code:
  note:
rec-032:
  state:
  reason_code:
  note:
rec-036:
  state:
  reason_code:
  note:
rec-040:
  state:
  reason_code:
  note:
rec-044:
  state:
  reason_code:
  note:
rec-048:
  state:
  reason_code:
  note:
rec-052:
  state:
  reason_code:
  note:
rec-056:
  state:
  reason_code:
  note:

approved_by: Pavol
effective_at: <leave blank until approval>
```
