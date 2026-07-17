# Pavol-Brain controlled rollout checklist

Operator checklist for deploying `write-safety-integrity-repair-spec.md` Packages 1–9 to `mini-core` and completing the Package 1 instance-marker backfill. This is the condensed, checkbox form of `docs/operations/brain-runtime.md`'s "Instance-marker backfill runbook", "Controlled deployment and rollout", and "Rollback plan" sections — read those in full before executing anything here; this checklist is a tracking artifact, not a substitute for the detailed procedure.

**Nothing on this checklist has been executed.** Checking a box is an operator action taken while performing the actual rollout, not a claim made by any docs/tests package.

## Pre-deploy

- [ ] `main` is clean and synced with `origin/main` (`git status --short` empty; `git status -sb` shows no ahead/behind)
- [ ] Acceptance suite green on the checkout to be deployed (`python scripts/run_brain_acceptance.py`, exit 0)
- [ ] Full suite green on the checkout to be deployed (`pytest tests/ -q`, 0 failed)
- [ ] Live backups verified — `docs/operations/brain-runtime.md` "Controlled deployment and rollout" step 1: `shasum -a 256` + copy of both journals and both retrieval DBs to a timestamped location
- [ ] Live audits referenced and current — Package 2 record-reference audit and Package 4 secret audit both show 0 findings (`docs/operations/brain-runtime.md` "Manual and live evidence" table); re-run only if write paths changed since
- [ ] Write-capable agents stopped (no active MCP session with a `write_enabled` profile)
- [ ] Projector LaunchAgents stopped (`launchctl bootout` both `com.pavol.brain-projector-{personal,work}`)

## Migration

- [ ] Personal marker backfill dry-run (`scripts/stamp_brain_instance.py --instance-id personal`, no `--apply`) reports `blocked: null`
- [ ] Personal marker backfill apply (`--apply`) reports `stamped: true`
- [ ] WORK marker backfill dry-run (`scripts/stamp_brain_instance.py --instance-id work`, no `--apply`) reports `blocked: null`
- [ ] WORK marker backfill apply (`--apply`) reports `stamped: true`
- [ ] Marker SQL verification — `SELECT * FROM brain_instance_identity` on both journals: exactly one row each, `instance_id` matches the journal's own directory
- [ ] Canonical content digest unchanged — the backfill script's own logical-digest check passed (built into apply; re-confirm via the row counts in the runbook's Step 7)
- [ ] Backups retained — `<journal>.pre-instance-stamp-backup.db` exists for both journals and is not deleted

## Retrieval

- [ ] Retrieval instance marker verified for both retrieval DBs (`run_brain_projector.py --instance-id <id> --validate`, read-only)
- [ ] Rebuild triggered for any retrieval DB reporting a missing/incompatible marker (delete + let the next projector run rebuild and re-stamp from empty)
- [ ] Projector plan/status healthy for both instances (`run_brain_projector.py --plan`, no `REBUILD_REQUIRED`/`FAILED`)
- [ ] Cursor consistent — plan output's cursor state matches the journal head for each instance (no unexpected backlog)

## Runtime

- [ ] `ControlStore` write profiles accepted — a write-enabled profile can be created/loaded for each now-marked instance journal
- [ ] Personal profile cannot open the WORK journal (`BrainConfig(instance_id="personal")` against the WORK file → `BRAIN_INSTANCE_MISMATCH`)
- [ ] WORK profile cannot open the Personal journal (reverse of the above → `BRAIN_INSTANCE_MISMATCH`)
- [ ] MCP read/write smoke — a real MCP client call against each instance succeeds for at least one read tool and (if intentionally exercising write) one write tool
- [ ] Scope isolation smoke — a scoped `get_related`/`search(include_artifacts=True)` call returns no out-of-scope id
- [ ] Idempotency replay smoke — a replayed write with the same explicit key and payload returns `idempotent: true`, zero new rows
- [ ] Artifact trust smoke — a `repo://`/`git://` artifact write surfaces the expected `verified_active`/`verified_inactive` state, not a client-asserted value

## Post-deploy

- [ ] Projector cycle healthy on both instances after restart (`HEALTHY` or `NO_CHANGES`, not `REBUILD_REQUIRED`/`FAILED`)
- [ ] Audit log clean — no unexpected error codes in the monitoring window (`docs/operations/brain-runtime.md` "Rollback plan" decision point)
- [ ] No `BRAIN_INSTANCE_*` errors from any live client in the monitoring window
- [ ] No `BRAIN_IDEMPOTENCY_*` surprises — any conflict seen is explained by an actual client replay with divergent metadata, not an unexplained collision
- [ ] No `REBUILD_REQUIRED` outstanding on either retrieval DB at the end of the monitoring window
- [ ] Rollback window maintained — backups and pre-deploy code SHA kept available for the agreed monitoring period before being considered final

---

See `docs/operations/brain-runtime.md` for: the full instance-marker backfill runbook (preflight, backup, digest capture, dry-run, apply, post-stamp verification, rollback, stop conditions), the full controlled-deployment step table (11 steps, with "must not run before" and per-step rollback), the full rollback plan (code / journal marker / retrieval DB / Control DB / LaunchAgent, and the rollback-vs-continue decision point), the acceptance/diagnostic command reference, and the manual/live evidence table. See `docs/architecture/write-safety-integrity-repair-spec.md` §11's closing commit map and §13's residual findings register for what is and is not closed in code/review as of this checklist's writing.
