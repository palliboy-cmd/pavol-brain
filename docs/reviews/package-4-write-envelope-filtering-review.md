# Package 4 review — canonical write-envelope filtering (commit fcbc2ee)

Reviewed 2026-07-16 against `docs/architecture/write-safety-integrity-repair-spec.md`
(B6, B7, I7, I8, §7, §10 rows 20–22, §11 Package 4). Baseline: `7a55c24`.
Scope: commit `fcbc2ee` only. Packages 1–3 and 5+ not re-reviewed.

## Verdict

**APPROVED WITH REQUIRED FOLLOW-UP**

B6 and B7 are closed at the level the invariants demand: secret non-persistence
(I8) holds byte-level across the journal DB, the retrieval DB, and the audit
JSONL in every probe run, and every client string reachable from a persisted
field — dict keys included, at any nesting depth — passes through the single
canonical scanner before any SQL (I7). The findings below are all echo-to-sender
or in-memory-only leaks plus one missing spec deliverable; none puts secret
bytes into persistent storage.

## Threat model

An agent-controlled MCP client (or a library caller) attempts to smuggle a
credential into local persistent storage — journal tables (including
`raw_input`), retrieval tables, or the on-disk audit JSONL — or to get it
reflected through server-controlled output that is stored or shipped
elsewhere. Package 4's specific attack surfaces:

1. a secret hidden as a **dict key** (`verification`), where the pre-Package-4
   scanner walked only `dict.values()` (B6);
2. a secret carried in **`request_id`**, the one free-form string written
   verbatim to the audit log by every operation, including the
   `policy_denial` line that fires *before* any `Brain` method runs (B7);
3. secondary reflection channels created by the fix itself: pydantic's
   value-echoing `ValidationError` rendering reaching `details`, `str`/`repr`,
   exception chaining (`__context__`/`__cause__`), FastMCP's `ToolError`
   wrapper, or the audit line written on the rejection path;
4. silent erosion: a future request-model field entering the write path
   unclassified.

## What was verified

### 1. Dict-key scanner (B6)

- `collect_client_strings` (`brain/write_policy.py`) is the only recursive
  walk in the codebase. Repo-wide grep for scanner-shaped code
  (`isinstance(value, dict)`, `value.values()`, `SECRET_PATTERNS`,
  `DENY_TEXT_PATTERNS`, `looks_like_secret`) finds no parallel write-path
  implementation. `enforce_band_c` is called exactly once, from
  `JournalWriter.record` (`brain/writer.py:109`), over
  `{"payload": payload, "client_metadata": metadata}` + provenance — i.e.
  payload, all write metadata (`idempotency_key`, `change_reason`, `links`,
  …) and provenance go through one gate. The only other user of
  `looks_like_secret` is the error-details gate in `brain/api.py::_record`
  (a filter on rendered error text, not a second scanner).
- The walk covers dict keys, dict values, nested dicts, lists, tuples, and
  dicts inside lists (unit test + independent probe with a dict-as-mapping
  nested inside an inner list — all six marker strings collected).
- `enforce_band_c` runs before `connect()` and before any SQL
  (`writer.py:109` vs `:128`); nothing is inserted before the scan passes.
- Probe: secret in a `verification` key (shape-valid variant, no `=`) →
  `BRAIN_WRITE_SECRET_REJECTED`, zero row delta, no canary bytes in journal,
  retrieval, audit, or returned error. Same for nested key/value/list
  combinations directly against `enforce_band_c`.

### 2. Verification-key constraint

- `VerificationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9 _./:-]{1,100}$")]`
  matches the spec's B6 row exactly (charset and 1–100 length via the
  pattern itself).
- Compatibility: keys like `tests/passed`, `step.1`, `run_id`, values with
  `-`/`:` all still validate; the read path (`get_record`,
  `RecordEnvelope.payload: dict[str, Any]`) never re-validates stored
  payloads, so existing persisted records with any key shape remain
  readable. (Low note: an idempotent *replay* of an old write whose key
  violates the new pattern now fails validation before the idempotency
  lookup — write-time only, acceptable.)
- The shape constraint does not replace Band C: a shape-valid secret key is
  caught by the dict-key scan (probe P1/M3), a shape-invalid one by pydantic
  (probe P2/M2); both proven independently.
- Error rendering for the shape-invalid + secret adversarial key
  (`api_key=sk-live-<canary>`, appears in pydantic's `loc` *and* `input`):
  canary absent from `str(exc)`, `repr(exc)`, `details`,
  `traceback.format_exception(...)` output, the MCP response JSON, and the
  audit line (which carries only `request_id` + `error_code`). The
  `looks_like_secret(text)` gate empties `details` whenever the rendered
  validation text matches a secret pattern. **But see finding F1:
  `__context__` still references the raw `ValidationError`.**

### 3. `request_id` enforcement (B7)

Every entry point accepting `request_id` validates it first:

| Entry point | `validate_request_id` before |
|---|---|
| MCP `brain_search`, `brain_get_record`, `brain_get_related`, `brain_record_outcome`, `brain_record_decision` | first statement in the tool body — before `resolve_scope`, before `authorize`, therefore before the `policy_denial` audit write in `RegistryPolicy._deny` |
| `Brain._request` (search), `get_record`, `get_related`, `_record` (all four `record_*`, incl. library-only `record_problem`/`record_analysis`) | first statement — before any journal access or audit write |
| `brain_health` / `brain_rebuild_status` | take no `request_id` |
| `control_center.py` | client-side; passes the literal `connection-test` (valid shape) |

Probed inputs: `""`, `" "`, `"  \t "`, `"id with space"`, 129×`x`,
`"semi;colon"`, embedded newline, the canary, `"café"`, an emoji — all
rejected with `BRAIN_INVALID_REQUEST`, `request_id == ""`, empty `details`,
no echo of inputs, **zero** journal-row delta and **zero** audit-log growth
(byte-compared). Boundary values length 1 and length 128 accepted and echoed
back only when valid. The B7 policy-denial probe (canary `request_id` +
non-granted workspace through a `RegistryPolicy`-backed server) returns
`BRAIN_INVALID_REQUEST` with no canary anywhere and no `policy_denial` line
written — re-run independently, matching
`test_b7_probe_rerun_invalid_request_id_rejected_before_policy_denial_audit`.

Residual (finding F3): `RegistryPolicy` itself has no floor — a *direct
library call* `policy.resolve_scope(..., request_id=CANARY)` writes the raw
canary into the `policy_denial` audit line (reproduced). No such caller
exists today; every MCP tool validates first. The B7 class re-opens if a
future tool forgets the one-line guard.

### 4. FastMCP deviation — verdict: **ACCEPTED**

Hypothesis reproduced in isolation against the installed
`mcp.server.fastmcp` (pydantic 2.13):

- A `pattern=`-constrained `request_id` on a raw tool signature →
  pre-body `ToolError` whose message embeds
  `input_value='api_key=sk-live-…'` verbatim.
- A `VerificationKey`-constrained dict key on a raw signature → pre-body
  `ToolError` embedding the raw key **twice** (in the error's `loc` path and
  in `input_value`).
- Mechanism confirmed in the installed package:
  `fastmcp/tools/base.py:117` — `raise ToolError(f"Error executing tool
  {self.name}: {e}") from e` — outside this codebase's control.

The body-level alternative that shipped returns a clean
`{"code": "BRAIN_INVALID_REQUEST", …, "details": {}}` JSON for the same
inputs (probes M1/M2). Keeping the signatures unconstrained and enforcing in
the body is therefore demonstrably safer, and the deviation is accepted.

Residual (finding F2b): the deviation does not — and cannot at this layer —
close pre-body validation entirely. The *existing* signature types still
trigger FastMCP validation: `verification: dict[str,str]` with a secret key
and a **non-string value** (`{"sk-live-<canary>": 12345}`) fails pre-body
and the `ToolError` echoes the secret key to the client (reproduced). Not
persisted, not audited (byte-checked). Pre-existing behavior, unchanged by
this package; fully closing it would mean untyped `dict`/`list` signatures
plus body-side coercion. Should be recorded in the spec as a known residual
alongside the compact-token one.

### 5. Write-envelope inventory

- `FIELD_CLASSIFICATION` covers every field of all seven request models —
  verified programmatically (`set(model.model_fields) ==
  set(FIELD_CLASSIFICATION[name])` for each; suite green).
- `OUT_OF_BAND_FIELDS` is exactly `{"request_id"}` with a documented reason
  (deliberately not a pydantic field on any write-path model, per B7) —
  appropriately narrow.
- Classifications spot-checked: `request_id`, `workspace`, `sensitivity`,
  `links`, `supersedes`, `idempotency_key`, `artifacts`, `commit`,
  `evidence` are `security_sensitive_control`; provenance text fields
  (`source_excerpt`, `source_ref`, `session_ref`) are
  `user_controlled_content`, consistent with §7.1 (they are Band-C-scanned
  content, while `source_assertion` — the band selector — is control). No
  misclassification found.
- Lock-in failure simulations (all behaved correctly):
  - injected field on `OutcomeRequest` → test fails naming
    `totally_new_field`;
  - stale classification entry (`ghost_field`) → test fails;
  - invalid bucket value → test fails;
  - cleanup → green again.
- Nested-model bypass: the two nested models reachable today
  (`RecordLink`, `DecisionAlternative`) are classified. A *future* nested
  model added inside an already-classified field would escape the inventory
  (finding F5, Low) — but not the Band C scan, which walks the dumped
  structure recursively at write time, so filtering safety is unaffected;
  only inventory completeness relies on review discipline. Likewise
  `REQUEST_MODELS` is hand-maintained: a brand-new top-level request model
  never added to it is invisible to the lock-in test.

### 6. Secret non-persistence (I8, §10 rows 20–22)

Canary matrix re-run via the suite (`test_secret_non_persistence_matrix`,
17 field positions + out-of-band `request_id`) and independently probed:

- byte-level absence of `sk-live` / full canary confirmed in the journal DB
  file, the retrieval DB file, and the audit JSONL after every rejection;
- zero persistent-row deltas in all cases;
- canary absent from error JSON, `str(exc)`, `repr(exc)`, and formatted
  tracebacks on the probed paths (exception: F2 below for non-URI-shaped
  secrets in `artifacts[]`/`evidence[]`, and F1 for `__context__`);
- B6 probe (both variants), B7 probe (Brain level + MCP policy-denial
  level), shape-invalid secret verification key, invalid-`request_id`
  policy-denial path, and library-only `record_problem`/`record_analysis`
  with canary `request_id` — all reproduced with the expected error codes
  and clean output.

### 7. Regression and scope

- `URI_RE` (Package 2 URI policy), typed-links validation, `classify`,
  artifact classification: byte-identical in the diff (only context lines).
- `brain/writer.py` untouched — Package 3 idempotency intact (suite's
  idempotency tests green).
- MCP tool list unchanged (`test_exact_tool_list_and_search_schema_parity`
  green); no SQLite schema change; exported-schema diff limited to
  `brain/schemas/v1/OutcomeRequest.json` (`verification`
  `additionalProperties` → `patternProperties`), `check_exported()` → True.
- No live journal/retrieval DB exists on this machine
  (`~/Library/Application Support/Pavol-Brain/personal` absent); nothing
  read or written outside temp fixtures. No feature creep found: the diff
  is exactly the ten files the changelog lists.

## Findings

### Blocking

None.

### High

None.

### Medium

- **F1 — `raise error from None` does not unchain the secret-bearing
  `ValidationError`; `__context__` still references it.**
  `brain/api.py:152`. PEP 415's `from None` sets `__suppress_context__` and
  clears `__cause__`, but `__context__` is still populated by the raise
  machinery inside the `except` block. Reproduced: after the shape-invalid
  secret-key rejection, `caught.__context__` is the original
  `ValidationError` whose text contains the full canary. Default renderers
  (`str`, `repr`, `traceback.format_exception`, the MCP `_error` JSON, the
  audit line) honor the suppress flag and leak nothing — verified — but any
  error-reporting integration or debugger that walks `__context__`
  unconditionally re-exposes the secret, and the changelog's claim that the
  original error "is not chained onto the returned BrainError via
  `__context__`" is factually wrong. Fix is one line (`error.__context__ =
  None` before the raise, or construct/raise outside the `except` block)
  plus a test asserting `exc.__context__ is None`.
- **F2 — a bare (non-URI-shaped) secret in `artifacts[]`/`evidence[]` is
  echoed verbatim in the returned error.** `validate_evidence_uris`
  (`brain/writer.py:106-107`) runs *before* `enforce_band_c` (`:109`) and
  raises `BRAIN_INVALID_ARTIFACT_URI` with `details={"values": [raw]}`.
  Reproduced: `artifacts=["sk-live-<canary>"]` → canary bytes in the
  returned error JSON. Not persisted, not audited (byte-checked); §7.2
  explicitly permits echoing syntax-invalid URIs as "inert", but §10 row 20
  requires the canary "absent from returned error JSON" for the
  `artifacts[]`/`evidence[]` positions, and the Package 4 matrix only
  exercised URI-shaped canaries (`doc://artifact/<canary>`), which pass
  syntax and hit Band C instead. Fix: swap the order so `enforce_band_c`
  runs first (it already scans these payload values), or gate the
  `values` echo through `looks_like_secret`. Also reconcile §7.2 with §10
  row 20 in the spec.
- **F2b — FastMCP pre-body validation of the *existing* signature types
  echoes raw values in `ToolError`.** See §4 above: a secret `verification`
  key paired with a non-string value leaks the key to the client through
  `fastmcp/tools/base.py:117`. Pre-existing, echo-to-sender only, outside
  this package's stated fix; record as a known residual in the spec.

### Low

- **F3 — no `request_id` floor at the audit sink.** `RegistryPolicy._deny`
  (`brain/control.py:160-162`) writes `request_id` verbatim to the
  `policy_denial` audit line; a direct library call with a canary
  `request_id` reproduces the B7 leak (no such caller exists today — every
  MCP tool validates first, per spec's chosen enforcement points).
  Cheap hardening: `validate_request_id` (or shape-check-and-blank) inside
  `_deny` or `AuditLogger.write`, so a future tool that forgets the guard
  cannot re-open B7.
- **F4 — the compact-token residual is real but accepted by the spec/changelog:**
  a credential that happens to match `^[A-Za-z0-9._:-]{1,128}$` (e.g. a
  bare `ghp_…`/`AKIA…` token without `=`) passes the `request_id` shape
  gate and is audit-logged. Documented as the accepted trade-off; noted
  here for completeness only.
- **F5 — inventory maintenance is manual at two levels** (`REQUEST_MODELS`
  membership; nested models inside classified fields). Consider deriving
  request models mechanically (e.g. from the `_record`/MCP call sites or a
  registry) in a later package.

## Test results

- Full suite at `fcbc2ee`: **184 passed + 5 subtests, 0 failed** (~10 s) —
  matches the changelog's claim (up from 174 + 5 at baseline).
- `check_exported()` → `True`.
- Independent probe scripts (disposable fixture journals, temp dirs only):
  26 checks core + 12 checks MCP/FastMCP; all passed except the three that
  *are* findings F1 (×2 probe variants) and F2 — i.e. every probe outcome
  is accounted for above.
- Manual reproductions required by the review scope: dict-key secret probe
  (both shape variants), invalid-`request_id` policy-denial probe (MCP,
  RegistryPolicy-backed), shape-invalid secret verification key
  error-rendering probe (str/repr/details/traceback/`__context__`/audit),
  `request_id` boundary lengths 1 and 128, inventory lock-in failure
  simulation (3 variants) — all executed, results as stated.

## Diff hygiene

`git diff 7a55c24..fcbc2ee`: 10 files, +420/−19, exactly the changelog's
list (4 source files, 1 new module, 1 regenerated schema export, 2 docs, 2
test files). No unrelated churn; comments in the new code state constraints
(B6/B7 rationale) rather than narration. The spec edits correctly preserve
the pre-package baseline text under a status header instead of rewriting
history.

## Required follow-up (ordered)

1. **F1:** clear `__context__` on the sanitized `BrainError`
   (`brain/api.py::_record`) + regression test; correct the changelog
   sentence.
2. **F2:** run `enforce_band_c` before `validate_evidence_uris` (or gate the
   URI echo through `looks_like_secret`) + extend the row-20 matrix with a
   non-URI-shaped canary in `artifacts[]`/`evidence[]`; reconcile §7.2 with
   §10 row 20.
3. **Missing Package 4 migration deliverable:** the spec's §11 Package 4
   Migration step — the *report-only* secret-pattern audit over both live
   journals' existing `payload`/`raw_input` — was not run (changelog
   explicitly says no live DB was read; no live journal exists on this
   machine). Run it wherever the live instance journals exist and attach the
   report before the §12 row-10 gate.
4. **F3 (recommended, small):** shape floor at the audit sink.
5. **F2b (documentation):** record the FastMCP pre-body type-failure echo as
   a known residual in the spec.

None of these reduces B6/B7 to open: persistence-level invariants I7/I8 hold
in every probe.

## Summary

| Item | Status |
|---|---|
| Verdict | **APPROVED WITH REQUIRED FOLLOW-UP** |
| B6 (dict-key secrets) | **Closed** — single canonical scanner walks keys/values/nesting; probes green; byte-level non-persistence proven |
| B7 (`request_id` audit leak) | **Closed** — validated before policy/audit/journal at every entry point; policy-denial path clean; residuals F3/F4 noted |
| FastMCP deviation | **ACCEPTED** — hypothesis reproduced (raw value in pre-body `ToolError`); body-level enforcement demonstrably safer; F2b residual documented |
| Most severe finding | F2 (Medium) — non-URI-shaped secret in `artifacts[]`/`evidence[]` echoed in returned error JSON, contra §10 row 20 (echo-to-sender only; nothing persisted) |
| Tests | 184 passed + 5 subtests, 0 failed; `check_exported()` True; all manual probes reproduced |
| Git status | branch `main` at `fcbc2ee`, working tree clean apart from this review file; **no commit and no push made by this review**; `fcbc2ee` remains unpushed (1 ahead of `origin/main`) |

---

## Required follow-up verification (2026-07-16, repair pass)

Scope: F1, F2, F2b, and the missing Package 4 migration deliverable (§ "Required follow-up" items 1, 2, 3, 5 above). F3 and F5 (Low) are out of scope for this pass and remain as filed. The original verdict above is preserved unchanged; this section records what closed and what is still open.

### F1 — CLOSED

`brain/api.py::_record` restructured: the sanitized `BrainError` is still built and audited inside the `except Exception` handler (unchanged), but the `raise` moved to *after* the enclosing `try`/`except` statement exits. Python attaches the currently-handled exception as `__context__` only to a `raise` executed inside an `except` block; once that block has exited normally, there is no handled exception to attach, so `__context__` is genuinely `None` — not just suppressed from rendering. No manual mutation of `__context__`/`__traceback__` and no `from None` trick; this is the "construct/raise outside the active `except` context" option the review suggested.

Verified (`tests/test_brain_write.py::test_f1_sanitized_write_error_carries_no_exception_context`, and manually reproduced independently): for the shape-invalid secret verification-key probe (`verification={"api_key=sk-live-<canary>": "ok"}`),
- `exc.__context__ is None`, `exc.__cause__ is None`
- canary absent from `str(exc)`, `repr(exc)`, `traceback.format_exception(type(exc), exc, exc.__traceback__)`, and `json.dumps(exc.details)`
- zero persisted rows

The Package 4 changelog entry in `write-safety-integrity-repair-spec.md` has been corrected in place (it previously asserted the original exception was "not chained onto the returned `BrainError` via `__context__`," which was false); a new dated changelog entry documents the actual fix.

### F2 — CLOSED

`brain/writer.py::record` reordered so `enforce_band_c` runs over the full write envelope (payload + metadata + provenance) before either `validate_evidence_uris` call. Both still run before `self.connect()`/`BEGIN IMMEDIATE`, so no SQL runs before either check passes.

Verified (`tests/test_brain_write.py::test_f2_bare_secret_in_artifact_fields_is_secret_rejected_not_uri_echoed`, parametrized over `evidence[]`, `artifacts[]`, `commit`, and `alternatives[].evidence[]` with a bare, non-URI-shaped canary in each position):
- every position now rejects with `BRAIN_WRITE_SECRET_REJECTED` (previously `BRAIN_INVALID_ARTIFACT_URI` with the canary echoed in `details.values`)
- canary absent from the error's `str()`/`repr()`/`details`, the journal DB file (byte grep), and the audit log (byte grep)
- `exc.__context__ is None` in all four cases (a plain `raise BrainError(...)` outside any `except` block, unaffected by F1 but checked anyway)
- zero row deltas across all four persisted tables

Regression check: the existing Package 2/B3 URI-rejection tests (`test_record_uri_is_rejected_in_evidence_artifacts_commit_and_alternatives_evidence`, `test_b3_probe_rerun_record_uri_evidence_is_rejected`) use non-secret-shaped `record://...` URIs, so they clear Band C unchanged and still fail at URI syntax with `BRAIN_INVALID_ARTIFACT_URI` and the same echoed (inert) `details.values` — no update needed, confirmed green as-is. §7.2 in the spec has been reconciled with §10 row 20 (the enforcement-order sentence now matches the code).

### F2b — CLOSED

`brain/mcp_server.py::brain_record_outcome`'s raw FastMCP signature changed `verification: dict[str,str] | None = None` → `verification: dict[str, Any] | None = None`. This is the review's preferred option 1 (smallest safe boundary change): `Any` means FastMCP's own pre-body pydantic model — built from the raw signature, validated before the tool body runs — no longer type-checks `verification`'s values, so a non-string value no longer triggers a pre-body `ToolError`. The value now reaches the tool body unchanged and flows into `Brain.record_outcome` → `OutcomeRequest(verification=...)`, whose domain-layer type (`dict[VerificationKey, str]`, unchanged) rejects it with the same pydantic `ValidationError` that F1's sanitized-error path already handles.

Verified (`tests/test_brain_mcp.py::test_f2b_probe_mcp_verification_secret_key_nonstring_value_no_pre_body_leak`, calling the real MCP tool boundary via `mcp.call_tool`, and manually reproduced independently before and after the fix):
- **before:** `mcp.call_tool("brain_record_outcome", {"verification": {"sk-live-<canary>": 123}, ...})` raised `mcp.server.fastmcp.exceptions.ToolError` with message `"...verification.sk-live-<canary>\n  Input should be a valid string..."` — the raw key embedded in the exception text, propagating out of `call_tool` entirely (not returned as tool-body error content)
- **after:** the same call returns normally with `{"error": {"code": "BRAIN_INVALID_REQUEST", "details": {}, ...}}`; no exception raised; canary absent from the response, the journal file, and the audit log

Schema/tool-surface impact confirmed nil: `check_exported()` → `True` (this change is on the raw FastMCP boundary, not any pydantic request/response model — `OutcomeRequest.verification` is untouched); `test_exact_tool_list_and_search_schema_parity` still green (tool names unchanged; it pins `SearchRequest`'s exported schema only — no test anywhere pinned `brain_record_outcome`'s raw `inputSchema`, so nothing needed updating there). No FastMCP-imposed blocker was hit; the fix did not require downgrading the domain schema.

### Missing Package 4 migration deliverable — CLOSED

**Update (2026-07-16, same day, operator run):** the live audit this session could not reach `mini-core` for (below) was subsequently run by the repo owner directly. Reported result:

- Method: `scripts/audit_write_envelope_secrets.py` run on the MBP against **read-only `scp` copies** fetched from `mini-core` (the same fetch-then-scan pattern used for the Package 2 live audit, `docs/reviews/package-2-record-reference-audit.md` §2.1) — the live `personal`/`work` journal files on `mini-core` were not modified.
- Result: **personal journal — clean; work journal — clean; 0 blocking findings; 0 informational-only findings.** No secret-shaped content found in `payload`, `raw_input`, provenance fields (`source_excerpt`/`source_ref`/`session_ref`), `artifact_links.artifact_uri`, `change_reason`, or `memory_events.data` (dict keys and values, any nesting) in either journal.

This closes the Package 4 §11 migration deliverable and the review's required-follow-up item 3. The original finding below (this follow-up session's own inability to reach `mini-core`) is preserved as a record of why the tooling was validated against fixtures only, not live data, in the session that built it.

### Missing Package 4 migration deliverable — session finding (superseded by the operator run above)

New `scripts/audit_write_envelope_secrets.py`: read-only (`mode=ro`, `PRAGMA query_only=ON`, `immutable=1` fallback for a WAL copy missing its `-wal`/`-shm` companions), reuses `brain/write_policy.py::looks_like_secret` as the sole detector, walks `payload`/`raw_input`/`memory_events.data` (parsed JSON, dict keys and values, any nesting) plus `source_excerpt`/`source_ref`/`session_ref`/`change_reason`/`artifact_links.artifact_uri`. Reports only `{journal label, record_id, event_id, field_path}` — never a matched value; a secret-shaped dict key is reported as a fixed `<dict-key:REDACTED>` path segment with zero characters of the key's own text, including in the paths of any values nested under it.

Validated against two disposable fixture journals (not the live instances):
- a fixture with a secret-shaped `verification` key inserted directly via SQL (bypassing the writer, simulating a pre-Package-4 or out-of-band row) → correctly flagged (`raw_input.payload.verification.<dict-key:REDACTED>`), no value printed
- a fixture written entirely through the real, now-fixed `Brain` write path → zero findings

**Could not be run against the real `personal`/`work` journals.** Per `docs/operations/local-development.md`, those live on `mini-core`, reached over SSH (`scripts/run_brain_mcp_ssh.sh`, default host alias `mini` / `mini-core.local`). Both `ssh mini` and `ssh mini-core.local` timed out from this follow-up session (no route to `192.168.50.109`) — an environment/network-reachability gap in this session, not a code defect. This session's local filesystem also has no `~/Library/Application Support/Pavol-Brain/{personal,work}` directories (only a `backups/` directory), matching the original review's finding for the *local* checkout; the live data itself was never in scope for the local machine, only for `mini-core`, and `mini-core` was unreachable here. Package 2's live audit was run successfully from a session that did have `mini-core` access (via `scp`, per `docs/reviews/package-2-record-reference-audit.md` §2.1) — this deliverable needs the same access, which this session does not have.

**Disposition at the time this session ended:** required follow-up, needing an operator with `mini-core` reachability to run
```
scripts/audit_write_envelope_secrets.py --journal personal=<personal journal path on mini-core> \
                                         --journal work=<work journal path on mini-core>
```
(read-only; safe to run directly over SSH against the live files, or against `scp`'d copies as Package 2 did). **This is exactly what happened next** — see the operator-run result above, which closes this item.

### Adversarial probe re-runs (this pass)

1. **F1 probe:** shape-invalid secret verification key (`verification={"api_key=sk-live-<canary>": "ok"}`) → `BRAIN_INVALID_REQUEST`, `__context__ is None`, `__cause__ is None`, canary absent everywhere checked. (Previously: same error code, but `__context__` held the raw `ValidationError`.)
2. **F2 probe:** bare canary in each of `evidence[]`/`artifacts[]`/`commit`/`alternatives[].evidence[]` → `BRAIN_WRITE_SECRET_REJECTED`, canary absent everywhere checked, zero row deltas in all four cases. (Previously: `BRAIN_INVALID_ARTIFACT_URI` with the canary in `details.values`.)
3. **F2b probe:** real MCP `brain_record_outcome` call with `verification={"sk-live-<canary>": 123}` → no exception propagates out of `mcp.call_tool`, controlled `BRAIN_INVALID_REQUEST` returned, canary absent from response/journal/audit. (Previously: `ToolError` raised with the raw key embedded in its message, before the tool body ever ran.)

### Test results (this pass)

- `tests/test_brain_write.py` + `tests/test_brain_mcp.py`: 36 passed (3 new: `test_f1_sanitized_write_error_carries_no_exception_context`, `test_f2_bare_secret_in_artifact_fields_is_secret_rejected_not_uri_echoed`, `test_f2b_probe_mcp_verification_secret_key_nonstring_value_no_pre_body_leak`).
- Full suite (`pytest tests/ -q`): **187 passed + 5 subtests, 0 failed** (up from 184 + 5 at `fcbc2ee`), run under both the project's default `.venv` (Python 3.14.6) and the `.venv-py311-backup` (Python 3.11) rollback runtime — identical result.
- `check_exported()` → `True`.

### Updated status summary

| Finding | Status |
|---|---|
| F1 (exception context) | **CLOSED** |
| F2 (URI-vs-secret ordering) | **CLOSED** |
| F2b (FastMCP pre-body dict-value leak) | **CLOSED** |
| Package 4 migration deliverable (live secret audit) | **CLOSED** — operator run against `scp`'d copies of the real `personal`/`work` journals from `mini-core`: 0 blocking findings, 0 informational-only findings, both journals clean |
| F3 (`request_id` floor at audit sink, Low, recommended) | not in scope for this pass — unchanged, still open |
| F5 (manual inventory maintenance, Low) | not in scope for this pass — unchanged, still open |

**Package 4 is now fully closed**: B6/B7 (from the original review) and F1/F2/F2b/the migration deliverable (from this follow-up) are all closed. F3 and F5 (both Low, both explicitly out of scope for this pass) remain open as filed and do not block Package 4 closure per the original review's own severity assessment.
