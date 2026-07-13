# M1 — Closed memory loop

Status: implemented in the repository; not deployed to mini-core and not committed.

## Decision: Personal and WORK isolation

M1 uses two physical Brain instances with one shared protocol and implementation:

- `personal`: its own canonical journal, derived retrieval index, projector and MCP profiles;
- `work`: its own canonical journal, derived retrieval index, projector and MCP profiles;
- no cross-instance retrieval or record links;
- `legacy`: the existing mixed journal remains read-only during migration and rollback.

This is compatible with the current topology because journal and retrieval paths are already runtime configuration, MCP is stdio per profile, and the projector is single-writer per database. M1 adds an immutable profile-to-instance binding and distinct LaunchAgent labels. It does not add a service, network endpoint, shared database, multi-master writer, or graph.

The canonical mini-core journal was inspected read-only before implementation: 55 records / 157 events, integrity `ok`, no foreign-key violations, schema version 1; workspaces are `abap-object-exporter`, `ai-pos`, `ai-pos-app`, `personal`, `sap-work`, and `smart-timesheet`. Only `sap-work` is marked sensitive. The intended partition is therefore the first five workspaces in Personal and `sap-work` in WORK. It is currently **blocked for live publish** by the legacy payload reference `rec-056.payload.source_record -> rec-001` (`sap-work` -> `ai-pos`). M1 does not rewrite that meaning automatically. The operator must explicitly curate a WORK-local replacement without a cross-instance record reference, or approve another partition that does not weaken the sensitivity boundary. Bootstrap audits documented payload fields, typed `record://` URIs, supersede state and event references and refuses every dangling/cross-partition target.

The split is a non-destructive export into new v2 journal files. It preserves record, event, materialized state, artifact relation, and artifact-validation identities. It never deletes or updates the legacy journal, and emits a hash/count manifest. Each derived index is then rebuilt from its new journal. Existing profiles migrate to `brain_instance=legacy`, `write_enabled=false`.

## Write contract

The public Python contract exposes narrow methods:

- `Brain.record_outcome(...)`
- `Brain.record_decision(...)`
- library-only typed `record_problem(...)` and `record_analysis(...)`

The MCP surface adds only `brain_record_outcome` and `brain_record_decision`. There is no `remember`, classifier, chat capture, review UI, context facade, projector mutation, or generic write tool.

Workspace defaults come from the server profile. Read calls may omit `workspaces`; a supplied list must be a subset of the profile. A write may omit `workspace` only when the resolved profile scope contains exactly one workspace. Profiles with broader scope must explicitly narrow to one. The caller cannot expand scope, choose identity, choose an instance, or self-grant sensitive/write access.

Every write stores server-owned `agent_id`, launcher/instance identity, timestamps, calculated confidence and policy result. Source assertion, excerpt and optional source/session references are client-declared inputs: the server validates and audits them, but does not present them as independently confirmed facts. `raw_input` is the validated normalized request (payload plus client metadata) after Band C rejection. Responses return record/event IDs, status, policy band and idempotency result. Audit output contains metadata and IDs, not payloads.

### Policy bands

- A: explicit user command/confirmation, authoritative documented decision, curated import, or verified outcome with at least one deterministically verified repository path/commit. The record starts `accepted`.
- B: agent inference, unconfirmed decision, verified outcome without an artifact, or an exact conflict with a current decision. The record starts `candidate` and is excluded from normal retrieval.
- C: secrets, credential-like high-entropy values, transcripts, chain-of-thought markers and raw stack traces are rejected before a record is created.

`authoritative_document` requires `source_ref`. Evidence and artifacts are typed URIs, but syntax alone is not validation. Repo paths use `git ls-files`; commits use `git cat-file`; unverifiable/invalid claims are audit-evented and cannot create a verified-tool Band A outcome. Idempotency keys are namespaced by server instance and agent identity and checked against the normalized request fingerprint. Same-content/different-key writes become explicit `possible_duplicate` candidates instead of a second Band A record.

### Decision and links

Decision schema v2 always contains `statement`, `rationale`, `alternatives`, `verdict`, `reason`, `reopen_when`, and `evidence`. Every alternative contains `option`, `verdict`, `reason`, `reopen_when`, and `evidence`.

Typed record links use active rows in the existing `artifact_links` table with `record://<record_id>` and one of `addresses`, `analyzes`, `decides`, `implements`, `results_in`, or `caused_by`. M1 rejects cross-workspace links; physical instance isolation additionally makes cross-instance targets impossible. `get_related` returns outgoing and incoming links but filters record targets through the caller's scope.

Supersede is append-only: a new accepted record is inserted, a `record_superseded` event is appended to the old record, and only the reproducible `record_state` fold changes. Target and replacement must have the same instance, workspace and type, the target must be accepted, and a reason is mandatory. Candidate records cannot supersede.

## Schema migration and deployment

Schema v2 only widens the `memory_records.type` CHECK constraint with `problem` and `analysis`; existing rows remain schema version 1 and keep byte-equivalent projected text/hashes. New writes use record schema version 2. No retrieval table change is required.

Preflight/dry run:

```sh
.venv/bin/python scripts/migrate_brain_m1.py --journal-db /path/to/journal.db
.venv/bin/python scripts/bootstrap_brain_instances.py \
  --source spike/spike.db \
  --personal-journal "$HOME/Library/Application Support/Pavol-Brain/personal/journal.db" \
  --work-journal "$HOME/Library/Application Support/Pavol-Brain/work/journal.db" \
  --personal-workspaces abap-object-exporter,ai-pos,ai-pos-app,personal,smart-timesheet \
  --work-workspaces sap-work \
  --manifest /path/to/reviewed-split-manifest.json
```

The dry run is expected to stop on `rec-056 -> rec-001` until an explicit operator-curated resolution exists. After reviewing a clean manifest, repeat bootstrap with `--apply`. Bootstrap uses a stable SQLite snapshot, builds both journals in staging, gates integrity/FK/count/partition/reference checks, rechecks the source digest, and publishes the pair with rollback cleanup. Targets must not exist. For any in-place v1 journal migration, `--apply` also requires an explicit unused `--backup` path and automatically restores the verified backup if postflight fails; an already-v2 journal returns before touching the backup path.

Build fresh indexes while no new profile is enabled:

```sh
BRAIN_BOOTSTRAP_MANIFEST=/path/to/reviewed-split-manifest.json \
  BRAIN_PYTHON="$PWD/.venv/bin/python" scripts/build_brain_m1_indexes.sh
```

Review both projector build manifests, run parity and zero-leak gates, then create distinct disabled Control Center profiles bound to Personal or WORK. WORK profiles require the `sap-work` sensitive grant. Run read-only and disposable staging write smoke tests through `RegistryPolicy`; do not enable writes on `legacy`.

Prepare both LaunchAgent plists without activation, then activate only after a second explicit approval:

```sh
BRAIN_BOOTSTRAP_MANIFEST=/path/to/reviewed-split-manifest.json BRAIN_M1_APPROVED=yes scripts/install_brain_m1_projectors.sh
BRAIN_BOOTSTRAP_MANIFEST=/path/to/reviewed-split-manifest.json BRAIN_M1_APPROVED=yes BRAIN_ACTIVATE_PROJECTORS=yes scripts/install_brain_m1_projectors.sh
```

Rollback is one unit: disable/revoke new profiles, boot out both M1 projectors, retain the two published journals/indexes and manifests for forensics, restore the Control DB backup and launcher configs, and point agents back to the unchanged `legacy` read-only profile. Never merge either M1 journal back into legacy as part of rollback.

## Acceptance evidence

`tests/test_brain_m1_acceptance.py` exercises the full flow with real journal files, real projector transactions, derived indexes and real `ControlStore`/`RegistryPolicy` profiles: two different Personal MCP identities and a separate sensitive WORK identity.

1. agent A reads existing context through its default profile scope;
2. agent A records an accepted outcome and decision;
3. the projector indexes both;
4. agent B searches and loads the outcome without conversation history;
5. agent B's default-off write is denied;
6. the identical outcome write returns the original record id with `idempotent=true`;
7. Personal cannot request WORK and WORK cannot request Personal;
8. direct SQL confirms neither journal contains rows from the other instance.

`tests/test_brain_write.py` additionally covers cross-agent duplicate semantics, deterministic artifact/commit verification and audit events, Band C across every persisted client text, scope-safe search links, outcome v2 projection and representative migration hash parity. `tests/test_brain_instance_bootstrap.py` proves that the real-shaped `rec-056 -> rec-001` payload link blocks publish; a separately curated safe fixture proves the 51/4 staging path, source immutability, cleanup, retry, count and integrity gates. Existing contract, projector, historical, artifact-validation, deterministic baseline, and zero-leak suites remain in the full test run.

## Deferred

Candidate approval/rejection remains an operator/review workflow; M1 does not add its UI or MCP tools. Deployment and live mini-core evidence require an explicit operator change window. Cross-instance retrieval, automatic hooks, `brain_context`, Obsidian export, Graphiti, knowledge loops, Slice 5 tuning and transcript storage remain out of scope.
