# M1 — Closed Memory Loop: Release Summary

- **Status:** Live accepted
- **Date:** 2026-07-13

## What M1 delivered

M1 turns Pavol-Brain from a read-only memory into a controlled shared memory loop. An agent can retrieve the context it is allowed to see, finish work, record an outcome or decision, and leave a durable handoff for another agent. This reduces repeated explanation while keeping the record auditable and bounded.

The release keeps the SQLite journal as the durable source of truth. It adds narrow, explicit write operations for outcomes and decisions rather than a generic memory endpoint. Writes are governed by server-owned profiles, workspace scope, provenance, idempotency, and safety filtering.

Personal and WORK memory are separate physical instances. Each has its own journal, derived retrieval index, projector, and MCP profile. The boundary is therefore structural: Personal data is not a queryable extension of WORK, and vice versa.

## What was verified

Live acceptance verified the complete handoff between distinct agent profiles: read existing context, write a governed result, retrieve it from another agent profile, and continue without conversational history. It also verified default-deny write access, idempotent retries, profile-bound scope, append-only history and supersede behavior.

The release verified that the Personal/WORK boundary has no cross-instance retrieval or links, and that derived indexes can be rebuilt from their journals without becoming authoritative. The legacy mixed journal remains unchanged as an audit and rollback source.

## Why this matters

The system now has a trustworthy memory lifecycle instead of only a search surface. Important work can survive agent, session, and time boundaries without relying on chat transcripts or informal recall. The design remains deliberately small: a local journal, rebuildable retrieval, and a governed MCP-facing contract.

## What follows

M1 is closed. Future work is intentionally separate and must earn its place through observed use: candidate review, offline operation and sync, a task-shaped context facade, automatic capture, export, knowledge loops, retrieval evaluation, and any future graph reassessment. The current backlog is maintained in [M2 Roadmap](../architecture/m2-roadmap.md).
