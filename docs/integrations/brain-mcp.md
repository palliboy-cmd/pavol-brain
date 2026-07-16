# Pavol-Brain MCP integration

The adapter uses official Python MCP SDK 1.x (`mcp>=1.27,<2`, resolved as 1.28.1) over stdio. M1 exposes the five read tools plus the narrow `brain_record_outcome` and `brain_record_decision` tools. It has no generic remember/delete, filesystem, shell, review, projector, migration, or build-switch tool. Write tools require both an allowed-tool entry and the separate profile write grant, which defaults off.

Run locally on mini-core with explicit configuration:

```sh
BRAIN_JOURNAL_DB=spike/spike.db BRAIN_RETRIEVAL_DB=sqlite-spike/retrieval.db \
BRAIN_ALLOWED_WORKSPACES=ai-pos,personal .venv/bin/python scripts/run_brain_mcp.py
```

Desktop clients launch a copied `~/bin/run_brain_mcp_ssh.sh` through SSH key authentication and stdio, with no forwarding or TCP listener. Install it from a checkout with `scripts/install_brain_mcp_client_launcher.sh`; Claude Desktop must not execute a launcher from `~/Documents`. The installer leaves a matching copy untouched and backs up a different regular file before replacing it. Read scope defaults to the profile and a request may only narrow it. A write may omit workspace only for a one-workspace profile. A caller-provided `sensitive_allowed=true` cannot create a grant: sensitive access requires an explicit server-side grant as well as request scope.

Every generated desktop configuration sets `BRAIN_MCP_SSH_HOST` and `BRAIN_MCP_REMOTE_ROOT` explicitly. The standard host is `mini`; the remote root is the Pavol-Brain checkout on mini-core. A copied launcher refuses to start without an explicit remote root unless it is itself colocated with a verified checkout. It checks the remote root, `.venv/bin/python`, and `scripts/run_brain_mcp.py` before starting MCP and prints an exact diagnostic for a missing prerequisite. `BRAIN_CLIENT_ROOT` is only a compatibility fallback for a colocated launcher. Do not commit host credentials, keys, tokens, or generated machine-specific client configs.

Hermes is configured under `mcp_servers.pavol-brain` in `~/.hermes/config.yaml`; remove with `hermes mcp remove pavol-brain`. Codex is configured under `mcp_servers.pavol-brain` in `~/.codex/config.toml`; remove with `codex mcp remove pavol-brain`. Claude Desktop uses `mcpServers.pavol-brain` in `~/Library/Application Support/Claude/claude_desktop_config.json`; remove only that JSON member and restart Claude. Restore the timestamped backups if targeted rollback is needed.

Agents preserve `record_id`, `source_event_id`, projection hash, and retrieval build ID. Profiles are bound to `personal`, `work`, or the read-only migration fallback `legacy`; a request cannot choose the instance. Runtime logs stay outside Git. ChatGPT remains outside this MVP.

Live status: Hermes 0.18.2 passed a real agent retrieval and preserved record/event provenance. Codex CLI 0.144.0 configured and its MCP definition is present, but the real agent smoke failed first on a missing `codex-code-mode-host` and then client-cancelled the direct call, so it is not an acceptance pass. Claude Desktop 1.20186.1 is configured but has no installed scriptable CLI for a real-agent smoke, so execution is not evaluated.

## Record-to-record references

A write can only point at another record through the typed `links` field on `brain_record_outcome`/`brain_record_decision` (`{"target_record_id": ..., "relation": "addresses"|"analyzes"|"decides"|"implements"|"results_in"|"caused_by"}`). The server resolves every link at write time — inside the same transaction as the rest of the write — and rejects it if the target does not exist, does not share the source's workspace, or is `rejected`/`forgotten`; a rejected link leaves no row anywhere.

`record://` is **not** an accepted artifact/evidence URI scheme. `evidence`, `artifacts`, `commit`, and `alternatives[].evidence` accept only `repo://`, `git://`, `adr://`, `route://`, `doc://`, and `workspace://` URIs — a `record://` value in any of these fields is rejected as an invalid URI, whether or not the record it names exists. Point at another record with `links`, not with an evidence/artifact URI.

## Idempotency contract

A replayed write with the same explicit `idempotency_key` and the same payload returns the original result with `idempotent: true`. Any divergence under the same stored key is a loud `BRAIN_IDEMPOTENCY_CONFLICT`, never a silent earlier-success return — including for these cases:

- **Legacy rows without a stored `request_hash`.** If the stored `record_created` event for that key predates `request_hash` tracking, replay ends in `BRAIN_IDEMPOTENCY_CONFLICT` (`details.reason="legacy_record_without_request_hash"`), even if the replayed payload is identical to what was originally recorded. The client must mint a new idempotency key to proceed — there is no way to make the same key succeed again.
- **The system never repairs history in place.** It does not retroactively rewrite the existing record or backfill the missing `request_hash` onto the stored event to make the key work again; the event ledger is append-only.
- **An explicit idempotency key names one logical write.** Reusing it for a different payload, different metadata (e.g. `supersedes`/`links`/`valid_at`/provenance), a different workspace, or a different record type is treated as an agent bug and fails loud with `BRAIN_IDEMPOTENCY_CONFLICT` rather than silently forking or overwriting.

## Write-envelope filtering (secret and `request_id` contract)

Every client-controlled string that could end up persisted anywhere — a payload field's value, a `verification` dict key, a nested list/dict element — passes through the same secret filter (Band C) before any SQL runs. This includes dict **keys**, not only values: a secret hidden as a `verification` key (e.g. `{"api_key=sk-live-…": "ok"}`) is rejected exactly like the same secret in a value, with `BRAIN_WRITE_SECRET_REJECTED` (or `BRAIN_INVALID_REQUEST` first, if the key's shape is also malformed — see below). A rejected secret is never persisted: no journal row, no audit log line, and no returned error ever contains the offending bytes.

`verification` keys additionally have a fixed shape: 1–100 characters from `A-Za-z0-9 _./:-` (letters, digits, space, underscore, dot, slash, colon, dash). A key outside that shape is rejected with `BRAIN_INVALID_REQUEST` before the secret scan even runs — this is a shape floor, not a substitute for the secret scan; both checks apply independently.

`request_id` (accepted by every tool) must match `^[A-Za-z0-9._:-]{1,128}$` — letters, digits, dot, underscore, colon, dash, 1–128 characters. It is a correlation token, not free-form content, so unlike payload fields it is shape-constrained rather than secret-scanned; the tight charset is what keeps it from carrying prose or delimiter-heavy secrets into the audit log. A malformed `request_id` is rejected with `BRAIN_INVALID_REQUEST` before any workspace/policy check, before any journal write, and before any audit log line for that call — the response's own `request_id` field is empty (`""`) and `details` is empty, so the invalid value is never echoed back or logged, even when the same call would also have been denied on workspace/scope grounds.
