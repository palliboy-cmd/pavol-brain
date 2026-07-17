# Package 6 — Artifact trust semantics review (B9)

- **Baseline:** `e8d1605`
- **Reviewed commit:** `2700ba9` — *fix(brain): expose server-owned artifact trust semantics*
- **Spec:** `docs/architecture/write-safety-integrity-repair-spec.md` (B9, I9, §8, §10 rows 23–24, §11 Package 6)
- **Reviewer scope:** Package 6 only. Packages 1–5 not re-reviewed. No implementation, commit, or push performed.

## Verdict

**REVISE PACKAGE 6**

Package 6 delivers the trust vocabulary, persisted verifier metadata, evidence
verification without band drift, the response-side `artifact_trust` object, and
the `Path("")`/cwd fail-safe fix — all of which check out. But it ships a
client-facing `verified_active` trust signal while a client-controlled artifact
URI can still forge that state and escalate an `outcome` write to **Band A
(accepted, auto-accepted)** with no real artifact. That is a direct violation of
invariant **I9** ("a syntactically valid URI alone never" produces `verified_*`)
in the exact function Package 6 revised and is responsible for. B9 is therefore
**not fully closed**. The fix is small (an options terminator on the git
invocations) but must land and be regression-tested before Package 7 builds on
these semantics.

**Update (2026-07-17): F1 and F2 are both CLOSED.** This verdict — the review's
findings and evidence as originally written below — is preserved unchanged as
the historical record. The repair and its verification are documented in
"Required follow-up verification" below and in
`docs/architecture/write-safety-integrity-repair-spec.md`'s Package 6 section
and 2026-07-17 changelog entry. Package 7 may now build on B9.

## Threat model

The write path is reachable by any trusted agent (an `agent_id` is required, but
that is the whole population of MCP callers). A caller controls `artifacts[]`,
`commit`, `evidence[]`, and `alternatives[].evidence[]` — free-form strings that
must pass `URI_RE` (`^(?:repo|git|adr|route|doc|workspace)://[^\s]+$`) only.
Adversary goals against the trust model:

1. Set/influence a server-owned trust field (`verified*`, `verifier`, `digest`, …) — target #1.
2. Make an unverifiable or non-existent artifact read back as `verified_active` — I9, target #7.
3. Escalate a write's policy band via evidence — target #2.
4. Verify against, or disclose, a repo the caller never configured — targets #6, #7.
5. Read a foreign record/repo identity through the new metadata — target #8.
6. Crash or mislead the read path with corrupt validation history — target #4.

The read side (`get_related`, `search(include_artifacts=True)`) is reachable by
any scoped reader; the concern there is trust-view honesty and fail-safe under
missing/corrupt state.

## Executed probes

All run against temporary journals (`journal_fixture`) and throwaway git repos;
no live journal or retrieval DB was opened. Reviewed checkout, `.venv` interpreter.

| # | Probe | Result |
|---|---|---|
| 1 | `doc://` evidence → `unverified_reference` (`reason=not_deterministically_verifiable`, `digest=null`) | PASS |
| 2 | valid `repo://` artifact → `verified_active` with `method/verifier/verified_at`(tz-aware `+00:00`)/`digest`(HEAD:path blob SHA) | PASS |
| 3 | valid `repo://` evidence → `verified_active` metadata persisted, band unchanged vs `doc://` twin (both Band B) | PASS |
| 3b | verifiable `evidence` cannot lift a Band-B decision to A | PASS |
| 3c | `alternatives[].evidence[]` verified with `relation=evidence` | PASS |
| 4 | unknown alias while `cwd` is a git repo → `unknown`/`repo_unavailable` for both `repo://` and `git://`; never verifies via cwd | PASS |
| 4b | missing alias (`repo_roots={}`) → outcome lands Band B, artifact `unverified_reference` | PASS |
| 5 | client self-assertion of `verified`, `verification_state`, `artifact_trust`, `verifier`, `verified_at`, `digest`, `method`, `object_digest`, `verifier_instance` → each `BRAIN_INVALID_REQUEST`, zero row deltas across all 5 tables | PASS |
| 6 | artifact_links row with no validation state → `{state: unverified_reference, everything else null}` | PASS |
| 7 | artifact deleted after verification → historical `verified_active`, `verified_at` and `digest` unchanged (point-in-time claim preserved) | PASS |
| A1 | nonexistent configured root → `unknown`/`repo_unavailable`, no path in output | PASS |
| A2 | malicious aliases (`../../../etc`, `..`, `-C`, `--help`, `$(rm -rf /)`, `a b`) not in roots → all `unknown` | PASS |
| A3 | traversal in relative part (`repo://alias/../../etc/passwd`) → never `verified_active` | PASS |
| A4 | repo path with spaces → verifies correctly with digest | PASS |
| A5 | symlinked configured root → deterministic `verified_active` | PASS |
| A6 | **argument injection via relative path** (`repo://pavol-brain/-v`, `--error-unmatch`, `--`, `-c`, `--exclude-standard`) → **`verified_active`** | **FAIL — see F1** |
| A7 | malformed `evidence` JSON in the joined `last_event_id` row via `trust_view` → **`JSONDecodeError`** propagates out of `get_related`/`search` | **FAIL — see F2** |
| A8 | serialized response contains no `/Users/…` filesystem path for forged/unknown-alias artifacts | PASS |

## B9 status

**Not closed.** The vocabulary, metadata, evidence verification, response object,
and cwd fail-safe are all present and correct, but I9's central promise — that a
syntactically valid URI alone can never earn `verified_*` — is violated by F1.
`classify()`'s artifact Band-A gate consumes the same forged `valid=True`, so the
defect also punches through the write-policy band model, not just the display
layer.

## Trust-state ownership assessment

Direct client self-assertion is **fully closed**. No request model
(`OutcomeRequest`, `DecisionRequest`, `ProblemRequest`, `AnalysisRequest`, or any
nested model) declares `verified`, `verification_state`, `artifact_trust`,
`verifier`, `verifier_instance`, `verified_at`, `digest`, `object_digest`, or
`method`; every model keeps `extra="forbid"`. Probe 5 confirms all nine names are
rejected as unknown fields with zero persistence, and there is no alias/nested
bypass. `verifier`, `verifier_instance`, `verified_at`, `object_digest`, and
`repo_alias` are written only by server code in `JournalWriter.record`
(`brain/writer.py`) and `verify()` (`brain/artifact_verifier.py`).

The gap is **indirect**: F1 lets a client steer the server verifier into
producing `verified_active` (with server-blessed `verifier`/`verified_at`) for a
non-existent artifact. That is arguably worse than direct self-assertion because
the false claim carries genuine server provenance.

## Evidence / band-drift assessment

**Correct.** `verify_all(artifacts + evidence, …)` is a pure per-URI map, so
adding evidence keys cannot change any `artifacts`/`commit` result. `classify()`
(`brain/write_policy.py:86`) reads only `payload["artifacts"]`/`payload["commit"]`
and is unchanged. Probes 3/3b and the two committed regression tests
(`test_evidence_verification_does_not_change_band_classification`,
`test_artifacts_only_band_a_gate_unaffected_by_package_6`) confirm evidence
verifiability never moves a record between bands, in either direction, for Band-A
and Band-B assertions and for decisions/alternatives. The projector eligibility
gate (`unresolved_relations`/`active_relations`) still keys only on
`artifact_link` records and is untouched.

## Alias / repository isolation assessment

**The Package 6 objective is met; a sibling injection is not.** The `Path("")`
→ cwd fallback is fully closed: both `repo://` and `git://` branches now check
`alias not in repo_roots` before any filesystem access and return
`unknown`/`repo_unavailable` with the alias name only. Probe 4 confirms an
unknown alias does not verify even when the process cwd is itself a git checkout.
No raw filesystem path, command stdout/stderr, or repo root appears in any
persisted metadata or response (probes A2/A8; `verify()` returns only
state/method/`repo_alias`/`digest`, and `_git_digest` returns only a stripped SHA
or `None`). Symlinked and space-containing **configured** roots resolve correctly.

What remains open is not alias isolation but **argument isolation** on the
already-resolved repo: the URI's relative-path / revision component is passed to
git without an options terminator (F1).

## Findings by severity

### F1 — HIGH — Option injection in `verify()` forges `verified_active` and escalates to Band A — **CLOSED (2026-07-17)**

`brain/artifact_verifier.py`: the `repo://` branch runs
`git -C <root> ls-files --error-unmatch <relative>` with no `--` guard. A
relative component beginning with `-` is consumed by git as an option, leaving
`--error-unmatch` with no pathspec to fail on, so the command exits 0:

```
verify("repo://pavol-brain/-v", {"pavol-brain": <root>})   # -> state: verified_active
git ls-files --error-unmatch -v            # rc=0  (no such file "-v" exists)
git ls-files --error-unmatch -- -v         # rc=1  (correct, with the -- guard)
```

Confirmed end-to-end through the write path:

```
record_outcome(artifacts=["repo://pavol-brain/-v"],
               source_assertion="verified_tool_result", …)
  -> policy_band="A", status="accepted", review="auto_accepted"
  -> artifact_trust = {state: verified_active, verifier: server-artifact-validator,
                       verified_at: <ts>, digest: null}
```

Payloads observed to mint `verified_active`: `-v`, `--error-unmatch`, `--`,
`-c`, `--exclude-standard`. Impact is bounded to `git ls-files` (read-only — no
file writes, no config/RCE; `-c core.editor=…` does not reach git as a
top-level flag), so the concrete damage is a **false existence verdict** →
forged `verified_active` → Band-A escalation of `outcome`+`verified_tool_result`
writes with no genuine artifact. `digest=null` is the only (undocumented)
distinguisher. Violates I9 and the §8 "Existing artifact" definition; defeats
the artifact Band-A gate this package is built around.

The vulnerable `ls-files`/`cat-file` lines predate Package 6, but they are the
verifier Package 6 owns and hardened (it fixed the analogous `Path("")` hole in
the same function), and Package 6 now surfaces the forged state as a first-class
client-facing trust claim. B9 cannot be considered closed while it stands.

**Failure scenario:** any writing agent calls
`record_outcome(artifacts=["repo://pavol-brain/-v"], source_assertion="verified_tool_result")`
and receives a Band-A, auto-accepted record whose artifact reports
`verified_active` — with no artifact named `-v` existing anywhere.

**Suggested fix (not applied):** insert an options terminator before the
user-controlled token in both branches —
`git ls-files --error-unmatch -- <relative>` and, for `git://`, resolve the
revision with a leading-`-`/`--end-of-options` guard (e.g. reject a `revision`
or `relative` beginning with `-`, or use `git rev-parse --end-of-options`). Add
a regression test asserting `repo://alias/-v` → `verified_inactive`.

### F2 — MEDIUM — `trust_view` raises on malformed validation-history JSON instead of failing safe — **CLOSED (2026-07-17)**

`brain/artifact_validation.py::trust_view` calls
`json.loads(state_row["evidence"])` with no guard. A malformed or non-JSON
`evidence` value on the joined `last_event_id` row raises `JSONDecodeError`,
which propagates out of `Repository.related()` and therefore out of
`get_related` and `search(include_artifacts=True)` — a read-denial for the whole
record. Review target #4 requires "malformed or inconsistent validation history
→ fail-safe, never verified"; this instead crashes the read.

Server code always writes valid JSON via `canonical(...)`, so this is not
client-reachable today; it fires only on already-corrupt/legacy data. Note the
commit already wraps the *query* in `try/except sqlite3.OperationalError` for
pre-table journals, showing the intended defensive posture — the JSON decode
just wasn't given the same treatment. It fails **closed** (crash), not into a
false `verified_active`, which is why this is Medium not High.

**Failure scenario:** a legacy/corrupt `artifact_validation_events.evidence`
value that is not valid JSON makes `get_related`/`search` on that record raise
`JSONDecodeError` (uncaught, not a `BrainError`).

**Suggested fix (not applied):** wrap the decode; on failure fall back to the
`state_row is None` fail-safe shape (`unverified_reference`, all metadata
`null`).

### Observations (no action required)

- `verified_inactive` and inconsistent `state=verified_active` with `evidence=null` both fold cleanly (probe; unit check).
- Timezone: `verified_at` is `writer.now()` = `datetime.now(timezone.utc).isoformat()` → always `+00:00`, persisted once and stable across reads (probe 7).
- `object_digest` semantics match the spec: `HEAD:<path>` blob SHA for `repo://`, resolved commit SHA for `git://`, `null` when not cheaply available; never a guess.
- Record-relation rows (typed links, `supersedes`/`superseded_by`, incoming) correctly never receive `artifact_trust` — only `NORMAL_ARTIFACT` URIs do.

## Test results

- `pytest tests/ -q` → **247 passed, 5 subtests passed** (baseline claim: up from 225+5). Zero failures, zero skips.
- `tests/test_artifact_validation.py` + `tests/test_brain_scope_integrity.py` → 56 passed.
- Package 6 write tests (`-k "trust or band or evidence or alias"`) → 14 passed.
- `brain.schemas.check_exported()` → **True**.
- MCP tool-list / search-schema parity (`tests/test_brain_mcp.py`) → 6 passed; tool list and signatures unchanged.

The committed suite is green because it does not include an option-injection
case (`repo://alias/-v`); that gap is why F1 shipped.

## Response scope interaction (Package 5)

Intact. `artifact_trust` rides on rows already produced and filtered by
`Repository.related()` → `Brain._scope_related`, so it appears only when the
relation itself is authorized and is absent with the relation when it is
filtered. Trust metadata exposes only `state/method/verifier/verified_at/
digest/reason` — no foreign record id, workspace, or repo path — and carries no
scoping decision of its own. Both response paths reuse the single `trust_view`;
no path reconstructs trust independently.

## Schema and compatibility

- `ArtifactTrust.json` is the only new/changed exported schema; `git diff` over `brain/schemas/v1/` shows every other file byte-identical. Additive, `additionalProperties:false`, `state` required, all else nullable with defaults.
- No SQLite schema change (validator metadata reuses the existing JSON `evidence` column).
- MCP tool list/signatures unchanged.
- Old records without metadata serialize safely: missing validation row → `trust_view(None)` fail-safe; `include_artifacts` consumers can ignore the additive field.

## Diff hygiene

Scope matches §11 Package 6 exactly: `brain/artifact_verifier.py`,
`brain/artifact_validation.py`, `brain/writer.py`, `brain/models.py`,
`brain/schemas.py`, `brain/repository.py`, new `brain/schemas/v1/ArtifactTrust.json`,
`docs/` (spec changelog + `brain-mcp.md`), and three test files. The one
pre-existing exact-equality assertion in `test_brain_scope_integrity.py` was
loosened to per-field asserts to accommodate the additive field — justified and
narrow. No unrelated files, no MCP surface change, no Graphiti/learning/sync
code. Comments are accurate and load-bearing.

## Remaining risks

1. ~~**F1 (HIGH):** client-forgeable `verified_active` + Band-A escalation via git option injection. Must fix before Package 7.~~ **CLOSED 2026-07-17** — see "Required follow-up verification" below.
2. ~~**F2 (MEDIUM):** `trust_view` crashes on corrupt validation-history JSON instead of failing safe.~~ **CLOSED 2026-07-17** — see "Required follow-up verification" below.
3. TOCTOU between verification and read is inherent and documented; `object_digest` is the intended drift-detection hook. No new risk. Not affected by the F1/F2 repair.

## Required follow-up verification

Repair landed 2026-07-17 on top of `2700ba9`, scoped to exactly F1 and F2 (no Package 7, no new verifier service, no remote fetching, no new artifact schemes, no SQLite migration, no live journal write).

**F1 fix** (`brain/artifact_verifier.py`): every client-controlled relative-path/revision token is now (1) rejected outright — before any subprocess runs — if empty, NUL-containing, absolute, escaping the resolved repo root, or beginning with `-`, and (2) passed to git with an explicit options terminator (`--` for `ls-files`/`cat-file -e`; `--end-of-options` for `rev-parse --verify` in `_git_digest`, since a bare `--` there is parsed as revision grammar rather than a terminator). No shell command string is built; every call remains an argument list with no `shell=True`.

- **repo:// option-injection tests** (`tests/test_artifact_validation.py::ArtifactVerifierArgumentIsolationTests`, `tests/test_brain_write.py::test_f1_repo_option_like_artifact_never_forges_band_a_or_verified_active`): `-v`, `--`, `--error-unmatch`, `-n`, `-c`, `--exclude-standard` — none reach `verified_active`, none reach Band A; a normal tracked file (`brain/api.py`) still verifies `verified_active`; a normal missing file stays `verified_inactive`; traversal (`../../etc/passwd`) and an absolute-looking relative (`/etc/passwd`) never verify; a NUL byte never crashes the check. A genuinely tracked file whose name begins with `-` is also rejected — a deliberate, documented conservative choice (the module's option-shaped-input guard takes precedence over that edge case; see the test's docstring) rather than a gap the `--` terminator alone would have left open.
- **git:// option-injection tests** (same files): revisions `-v`, `--help`, `--`, `-n` — none reach `verified_active`; the real `HEAD` SHA still verifies `verified_active` with a matching `object_digest`; an invalid-but-normal 40-`0` SHA stays `verified_inactive`; the subprocess calls remain read-only (`ls-files`, `cat-file -e`, `rev-parse --verify`) with no shell execution anywhere.
- **End-to-end probe** (`tests/test_brain_write.py::test_f1_end_to_end_fable_probe_no_longer_bands_a_or_verifies_active`, manually reproduced independently of pytest): `record_outcome(artifacts=["repo://pavol-brain/-v"], source_assertion="verified_tool_result")`.
  - **Before (baseline, as documented above):** `policy_band="A"`, `status="accepted"`, `review="auto_accepted"`, `artifact_trust.state="verified_active"`.
  - **After:** `policy_band="B"`, `status="candidate"`, `review="pending"`; the underlying validation event's `state` is `verified_inactive` (`reason_code="malformed_uri"`), `object_digest` is `null`, and no filesystem path or subprocess output appears in the persisted evidence JSON.

**F2 fix** (`brain/artifact_validation.py`): `trust_view()` now catches a `json.loads` failure and any non-`dict` parse result (list, string, number, `null`) — and a missing `evidence` column/key — and folds to the same fail-safe shape as a missing validation row (`unverified_reference`, all metadata `null`, `reason="malformed_validation_metadata"`), even when the joined row's own `current_state` says `verified_active`. The raw corrupt value is never echoed. A missing-or-empty (but not malformed) `evidence` value keeps the pre-existing, already-reviewed "folds cleanly" behavior unchanged.

- **Unit tests** (`tests/test_artifact_validation.py::ArtifactTrustViewFailSafeTests`): invalid JSON text, empty string (unchanged behavior, not a regression target), JSON list, JSON scalar (int/string/bool), JSON `null`, an object with wrong-typed fields (no crash, values pass through — still a valid object, not malformed), a missing `evidence` key (no `KeyError`), and the exact review scenario — `current_state="verified_active"` with malformed `evidence` — resolves to `unverified_reference`, never `verified_active`.
- **Integration tests** (`tests/test_brain_write.py::test_f2_malformed_validation_evidence_fails_safe_on_get_related`, `::test_f2_malformed_validation_evidence_fails_safe_on_search_include_artifacts`): a record written normally through `record_problem` (validation event legitimately reaches `verified_active`), then its `artifact_validation_events.evidence` column corrupted directly via SQL. Both `get_related` and `search(..., include_artifacts=True)` return `unverified_reference` with no exception and no echoed corrupt payload.

**Regression:** `pytest tests/ -q` → 277 passed, 21 subtests passed (up from 247 + 5 at `2700ba9`), zero failures/skips. `check_exported()` → `True` (no schema change). `tests/test_brain_mcp.py` (MCP tool-list/search-schema parity) → 6 passed, tool list unchanged. `git diff --check` → clean (no whitespace errors). Every pre-existing Package 6/5/4 trust, band, scope, and secret-filter test in `tests/test_brain_write.py`, `tests/test_artifact_validation.py`, and `tests/test_brain_scope_integrity.py` still passes unchanged.

**Diff scope:** `brain/artifact_verifier.py`, `brain/artifact_validation.py`, `tests/test_artifact_validation.py`, `tests/test_brain_write.py`, this file, `docs/architecture/write-safety-integrity-repair-spec.md`. No commit or push was made by the review itself; the repair was committed separately (see the spec's 2026-07-17 changelog entry for the commit reference).

## Confirmation

- **Git status:** clean working tree; branch is `main` at `2700ba9` (one commit ahead of `origin/main`, pre-existing).
- **No commit or push was created by this review.** Only this report file was added under `docs/reviews/`.
