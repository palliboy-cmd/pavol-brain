# ADR-001: M1 Live Acceptance

- **Status:** Accepted
- **Date:** 2026-07-13

## Context

Pavol-Brain already had an append-only SQLite journal and read-only retrieval, but an agent could not safely close a unit of work for another agent to continue. M1 addressed that missing handoff while preserving the journal as the canonical source of truth.

## Decision

M1 establishes the closed memory loop: an agent reads scoped context, completes work, records a governed outcome or decision, and another agent can retrieve that record without reconstructing the history from conversation.

The accepted architecture is:

- Personal and WORK are physically separate Brain instances, each with its own journal, derived index, projector, and MCP profiles; cross-instance retrieval and links are not permitted.
- The SQLite journal remains append-only canonical truth. Retrieval indexes are derived and rebuildable.
- The public write surface remains narrow: outcomes and decisions only. Writes are profile-governed, default-deny, scoped by server-owned identity and instance, and preserve provenance, idempotency, policy-band handling, and secret protection.
- Decisions carry their alternatives and reopening conditions; `problem` and `analysis` are first-class record types. Record relationships use typed `record://` links within an instance.
- The legacy mixed journal remains a read-only audit and rollback source. The approved, snapshot-bound exclusion of the legacy synthetic relation is a migration curation decision, not runtime knowledge in either instance.

## Verification

Live acceptance confirmed the complete cross-agent handoff, default-deny writes, idempotent retry behavior, scope enforcement, and Personal/WORK zero-leak isolation. It also confirmed journal integrity, deterministic derived-index rebuilding, append-only and supersede invariants, and non-destructive migration from the legacy audit source.

## Consequences

M1 is complete. Future work must preserve the instance boundary, journal authority, and narrow governed write model unless a later ADR explicitly changes them.

## Out of scope

M1 does not introduce offline operation or sync, `brain_context()`, automatic agent capture, candidate-review UI, Obsidian/Markdown export, knowledge loops, graph projection/Graphiti, retrieval tuning, or transcript storage. These remain backlog topics, not implicit extensions of M1.
