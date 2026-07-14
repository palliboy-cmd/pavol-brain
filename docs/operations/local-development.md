# Local development environment

This describes the local MBP development checkout. It is not the authoritative deployment: the live Pavol-Brain services — Control Center, projector LaunchAgents, canonical journals, and retrieval databases — run on mini-core, reached from clients through the SSH stdio MCP launcher (`scripts/run_brain_mcp_ssh.sh`). Nothing in this document changes that; the local checkout exists for development, tests, and local tooling only.

The mini-core production checkout is kept in sync with this repository by explicit file-content parity (SHA256 comparison against the validated commit for every tracked file), not by `git pull` — mini-core does not have a `.git` directory. Treat mini-core as a synchronized copy, not a git remote; changes reach it only via a deliberate, verified sync step.

## Supported and preferred Python versions

`requires-python = ">=3.11"` in `pyproject.toml` remains intentional and unchanged — do not narrow it. Both the local MBP `.venv` and the mini-core production `.venv` now run **Python 3.14.6** (uv-managed); `.python-version` pins 3.14.6 for uv. **Python 3.11.15 remains a supported rollback runtime** on both machines — see [Rollback](#rollback) below. Do not introduce 3.14-only syntax without first validating on 3.11, since 3.11 is the documented fallback, not merely a historical artifact.

Hermes (the separate MCP client/gateway used to reach Pavol-Brain) is **independently managed and remains on its own Python 3.11 environment** (`~/.hermes/hermes-agent/venv`) on both machines. It is not part of this project's `pyproject.toml`/`uv.lock` and was not touched by this migration — do not change Hermes's Python version as part of any Pavol-Brain work.

`/usr/bin/python3` (the Apple/Command Line Tools Python) is untouched on both machines and must not be modified, relinked, or relied upon — nothing in this project uses it.

### Migration history

- Local MBP `.venv` validated and cut over to Python 3.14.6 in commit `43db02d` (`build: migrate local Pavol-Brain dev environment to Python 3.14.6`) — full test suite passed unchanged, `uv.lock` required no modification, all native dependencies (`cryptography`, `pydantic-core`, `cffi`, `rpds-py`) resolved as prebuilt wheels.
- A real, pre-existing SQLite connection-lifecycle defect was found during that validation: several `brain/` modules relied on garbage collection rather than explicit `close()` to release connections. This was invisible under Python 3.11 but caused file-descriptor exhaustion under Python 3.14's more incremental garbage collector, especially under a constrained `ulimit -n`. Fixed in commit `785c83f` (`fix: explicitly close SQLite connections across brain/`), which converts every connection lifecycle to an explicit `contextlib` context manager and adds a regression test suite (`tests/test_sqlite_connection_hygiene.py`) that fails against the pre-fix code and passes on Python 3.11.15, 3.13.13, and 3.14.6.
- mini-core production was migrated to Python 3.14.6 after both commits were synced there by file-content parity. Production validation covered Control Center health, a fresh MCP connection with all read-only tools, retrieval search against the real active index, projector plan/validation and an observed real production cycle, `PRAGMA quick_check`/`foreign_key_check` on all five production databases, and a 10+ minute stability observation with a bounded, non-growing file-descriptor count. No dependency or `uv.lock` change was required for the production cutover either.

## Environment setup

Requires `uv` (any reasonably current version; validated with uv 0.11.x) and no other manual Python install — `uv` provisions the pinned interpreter itself.

```sh
uv venv .venv --python 3.14
uv sync --locked --group dev
```

`--locked` fails instead of silently re-resolving if `pyproject.toml` and `uv.lock` have drifted. To intentionally update a dependency: edit the constraint in `pyproject.toml`, run `uv lock`, review the diff, then `uv sync --locked --group dev` again and rerun the full test suite before committing `pyproject.toml` and `uv.lock` together.

## Test commands

```sh
uv run pytest tests/
```

Scope explicitly to `tests/` rather than running bare `pytest` from the repo root. `spike/` is a separate, self-contained historical experiment with its own `spike/.venv` and its own dependencies (including `graphiti_core`, not part of this project's dependency graph); an unscoped `pytest` invocation will also try to collect `spike/tests/` and fail there for unrelated reasons.

## Safe local smoke tests

All of these use `tests/journal_fixture.py` or another disposable path under `/tmp` — never a real journal, retrieval database, or control database:

```sh
# Build a disposable fixture journal
python -c "import sys; sys.path.insert(0,'tests'); from journal_fixture import journal_fixture; journal_fixture('/tmp/x/journal.db')"

# MCP server: starts and speaks stdio JSON-RPC against the fixture
BRAIN_JOURNAL_DB=/tmp/x/journal.db BRAIN_RETRIEVAL_DB=/tmp/x/retrieval.db BRAIN_STATE_DIR=/tmp/x/state \
  BRAIN_INTEGRATION_ID=dev BRAIN_CLIENT_IDENTITY=dev .venv/bin/python scripts/run_brain_mcp.py

# Control Center: an alternate loopback port, disposable control DB
BRAIN_STATE_DIR=/tmp/x/state BRAIN_CONTROL_DB=/tmp/x/control.db \
  .venv/bin/python scripts/run_brain_control_center.py --host 127.0.0.1 --port 18765

# Projector: read-only plan against the fixture journal, no writes
.venv/bin/python scripts/run_brain_projector.py --journal-db /tmp/x/journal.db --retrieval-db /tmp/x/retrieval.db --plan
```

**Never** point `BRAIN_JOURNAL_DB`, `BRAIN_RETRIEVAL_DB`, or `BRAIN_CONTROL_DB` at a real path under `~/Library/Application Support/Pavol-Brain`, `spike/spike.db`, or `sqlite-spike/retrieval.db` for a local smoke test or experiment — those are real (or rollback-source) local state, not test fixtures, even though the config defaults fall back to the `spike/`/`sqlite-spike/` paths when the environment variables are unset. Always set the environment variables explicitly.

`scripts/run_brain_mcp_ssh.sh` and the `install_brain_*.sh` scripts touch real local machine state (LaunchAgents, `~/Library/LaunchAgents`, `~/bin`) or a real remote host over SSH — do not run them as part of routine local development; they are one-shot operator actions, not dev-loop commands. `tests/test_brain_mcp_launcher.py` exercises the SSH launcher's quoting and error paths safely, by stubbing `ssh` on `PATH` so no network connection is ever made.

## Rollback

Python 3.11.15 is the documented, supported rollback target for both the local `.venv` and mini-core's production `.venv` — recreate it directly from the unmodified lockfile rather than relying on a timestamped backup directory being present (backup directories are local, disposable artifacts of past migrations and should not be assumed to exist):

```sh
mv .venv .venv-py314-failed-$(date -u +%Y%m%dT%H%M%SZ)   # keep the failed/suspect env aside for diagnosis
uv venv .venv --python 3.11
uv sync --locked --group dev --python 3.11
.venv/bin/python --version    # expect 3.11.15
uv run pytest tests/          # confirm the baseline still passes
```

This uses the exact same unmodified `uv.lock` as the 3.14.6 environment — `requires-python = ">=3.11"` and the lockfile support both without any dependency changes, so no lock regeneration is needed to roll back.

On mini-core, the same commands apply after stopping the affected LaunchAgent first — see [brain-control-center.md](brain-control-center.md) for the exact production stop/restart sequence and the important macOS TCC caveat when restarting a LaunchAgent.
