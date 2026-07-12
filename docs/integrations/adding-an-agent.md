# Adding an MCP agent

Open the Control Center through the documented SSH local forward and choose **Add integration**. Select `custom_mcp` for any generic MCP client, give it a stable lowercase ID, select local or SSH stdio, explicitly select workspaces and tools, leave sensitive grants empty unless separately approved, and create it disabled.

The detail page generates a profile-specific Hermes command, Codex command, or Claude/custom JSON fragment. Copy only that fragment into the client. The launcher supplies `BRAIN_INTEGRATION_ID`; agent requests cannot choose identity. Start with the profile disabled, inspect the generated configuration, explicitly enable it, then run the connection test.

The test performs a real MCP handshake, verifies the exact five tools, health, an allowed search, ungranted and sensitive denial, provenance, audit identity, and byte-identical journal/retrieval DBs. Its calls carry `test_call=true` and are excluded from real-use metrics.

Grant changes are targeted profile updates and create append-only events with actor, before/after policy hashes, changed fields, time and reason. Sensitive access requires both an allowed workspace and a sensitive grant. To suspend access, disable the profile; to permanently remove access, revoke it. Revocation keeps history. Remove the corresponding client fragment afterward.

Operational state is in `~/Library/Application Support/Pavol-Brain/brain-control.db` on mini-core. Back it up using SQLite `.backup` before migrations or restore a timestamped installer backup for rollback. Never copy runtime DBs, audit logs, client configs, SSH keys or secrets into Git.

This is trusted single-user local-process isolation, not a security boundary against a malicious process already running as Pavol. No public remote authentication, HTTP MCP endpoint, tunnel, or WAN route is part of the design.
