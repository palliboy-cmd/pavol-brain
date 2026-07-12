# Proposal 008: Canonical artifact validation

- **Status:** Accepted and completed — migrated and backfilled on the canonical mini-core journal 2026-07-12
- **Scope:** additive canonical-journal design required before Slice 2 can complete

> **Completion note (2026-07-12).** The additive migration (`spike/schema/artifact_validation.sql`,
> applied via `scripts/apply_artifact_validation_migration.py` after a verified SQLite backup) and the
> approved backfill (`scripts/backfill_artifact_validation.py` over
> `sqlite-spike/results/artifact-validation-approved.json`) are live on the canonical journal.
> All fourteen relations were explicitly reviewed and approved by Pavol (effective
> 2026-07-12T00:00:00+02:00): thirteen `verified_active`/`manual_verified`, and
> `rec-048` `verified_inactive`/`wrong_target` — **because of explicit human fixture approval,
> not because the file is missing**. Validation is relation-level (`artifact_link_id`);
> filesystem reachability remains diagnostic only; the approval history is append-only
> (a second backfill run inserted zero events); new artifact relations without a validation
> judgement block projection with `REBUILD_REQUIRED` instead of being guessed.
> Evidence: `sqlite-spike/results/artifact-validation-migration-canonical.json`,
> `artifact-validation-backfill-report.json`, `artifact-validation-backfill-rerun.json`.
> All acceptance criteria in §"Acceptance criteria and sequencing" were met by the Slice 2
> live gate (`brain-slice2-projection.json`, `brain-slice2-live.json`).

## Problem and boundary

The Slice 2 projector correctly stopped with `REBUILD_REQUIRED`: the journal has fourteen
`artifact_link` memory records but zero `artifact_links` rows or equivalent validation facts.
The historical fixture baseline excluded `rec-048`, but its fixture-only judgement is not
canonical evidence. A projector must never infer that judgement from today's checkout,
filesystem, network reachability, or a baseline JSON file.

Three concepts are intentionally separate:

1. **Memory record state** remains the current journal authority: `candidate`, `accepted`,
   `superseded`, `rejected`, and `forgotten`.
2. **Artifact relation validity** is a reviewable canonical fact about a typed relationship:
   URI correctness, target correctness, ownership/workspace correctness, duplication, or
   replacement. It is not implied by memory-record acceptance.
3. **Target reachability** is a time-and-environment diagnostic: `exists`, `missing`,
   `inaccessible`, `offline_host`, or `unknown`. It may be evidence for review, but cannot
   silently alter canonical relation validity.

## Granularity and state model

Validation is per **artifact relation**, not merely per memory record. The existing
`artifact_link` record currently has one payload relation, but a future record can own more
than one `artifact_links` row. The stable subject is therefore `artifact_link_id`, formed by
the artifact record ID plus `artifact_uri` and `relation`; a record-level convenience view is
permitted only when it has exactly one relation.

Canonical states are deliberately small:

- `unknown` — no human-approved relation judgement;
- `verified_active` — relation is canonical and indexable;
- `verified_inactive` — relation was explicitly judged not current/usable as a relation.

Canonical reasons: `manual_verified`, `wrong_target`, `malformed_uri`, `duplicate`,
`superseded`, `intentionally_retired`, `migrated_from_baseline_review`, and `other`.
`target_missing` and `access_unavailable` are diagnostic observations, not sufficient on
their own to write either verified state.

## Additive append-only model

```sql
CREATE TABLE artifact_validation_events (
  event_id TEXT PRIMARY KEY,
  artifact_link_id TEXT NOT NULL,
  artifact_record_id TEXT NOT NULL REFERENCES memory_records(record_id),
  artifact_uri TEXT NOT NULL,
  relation TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  effective_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('unknown','verified_active','verified_inactive')),
  reason_code TEXT NOT NULL,
  actor TEXT NOT NULL,
  source TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '{}',
  note TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  supersedes_validation_event_id TEXT REFERENCES artifact_validation_events(event_id)
);
CREATE INDEX artifact_validation_events_link_time
  ON artifact_validation_events(artifact_link_id,effective_at,event_id);

CREATE TABLE artifact_validation_state (
  artifact_link_id TEXT PRIMARY KEY,
  artifact_record_id TEXT NOT NULL REFERENCES memory_records(record_id),
  current_state TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  effective_at TEXT NOT NULL,
  last_event_id TEXT NOT NULL REFERENCES artifact_validation_events(event_id),
  validated_by TEXT NOT NULL,
  evidence_reference TEXT NOT NULL
);
```

The state view is a deterministic fold ordered by `effective_at, event_id`; it is recreated
from events, never edited as authority. Migration is additive only: take a canonical DB
backup, add tables/indexes, verify the empty fold, and leave every existing relation
`unknown`. Rollback drops only the new tables after confirming no approved events exist;
existing records and events are never updated.

## Eligibility and temporal semantics

For an accepted or superseded artifact record, the projector resolves the relation state as
of the retrieval request/build time.

- `verified_active`: project the record and its explicit typed relation.
- `verified_inactive`: remove it from current retrieval. It remains available to historical
  retrieval only for an `as_of` before the inactive event's `effective_at`; afterwards it is
  absent. This supports correction timelines without treating an invalid relation as current.
- `unknown` or missing: a full build returns `REBUILD_REQUIRED` with relation IDs. It never
  probes the filesystem and never guesses.

A superseded memory record is historical according to the existing record-state rules; an
artifact validation event independently determines whether its relation was valid at a given
time. A relation-level invalidation removes that relation; if the memory record has no other
active relations, its artifact document is removed. No inferred edges are created.

## Review-first writes and backfill

Only a named human reviewer or trusted operator workflow may write `verified_active` or
`verified_inactive`. A future automated validator may write a separate observation/candidate
event, but cannot promote it. Corrections append a new validation event, naming the prior
event where applicable, and preserve actor, source, reason, and evidence.

Backfill is two steps:

1. Generate `artifact-validation-backfill-plan.json`: all fourteen rows, suggested state and
   reason, evidence, confidence, and `approved: null`. This is read-only.
2. After explicit per-row approval, import idempotent events using a stable key such as
   `artifact-validation:v1:<artifact_link_id>:<approved-state>`. Record the reviewer and
   evidence; then emit an audit report. Do not update `memory_records`, `memory_events`, or
   historical benchmark files.

## The historical exclusion

`rec-048` is absent from the 51-document baseline because
`sqlite-spike/scripts/fts_baseline.py::eligible()` excludes an `artifact_link` when fixture
field `expected.artifact_validation != 'valid'`; its fixture value is `invalid` for
`repo://ai-pos/missing-file.ts`. This is a reproducible **baseline-only heuristic**, not a
canonical reason. The current mini-core checkout has no accessible `ai-pos` repository, so
reachability is `inaccessible`, not proof of `wrong_target` or `target_missing`.

The proposed backfill marks `rec-048` `unknown`, `requires_manual_review: true`, with a
suggested review question: should this relation be `verified_inactive` because the URI was
wrong/missing at the relevant effective time, or should it be corrected/superseded by a
different artifact relation? No state is implied by this proposal.

## Slice 2 integration after approval

The journal reader will fold validation events and return canonical relation state. Projection
hash includes relation state/effective facts only when they affect the indexed representation.
Validation-event replay is idempotent; derived cursor movement remains in the same retrieval
transaction as document/vector changes. Ranking, embeddings, public `brain.Brain` API,
contract schemas, and baseline evidence do not change.

## Acceptance criteria and sequencing

Before closing Slice 2: all 14 relations are explicitly reviewed; `rec-048` has an
auditability-backed reason; no state derives solely from reachability; full projection reaches
51 documents/51 embeddings, contract parity 24/24, zero leaks, a no-change second run with
zero embedding requests, consistency PASS, unchanged journal/active baseline hashes, and
p95 ≤ 50 ms.

Future commits must remain separate:

1. `Add canonical artifact validation journal model`
2. `Backfill reviewed artifact validation states`
3. `Complete incremental projection Slice 2`
4. `Record Slice 2 live validation evidence`

This proposal authorizes none of those writes.
