# Local development environment

This describes the local MBP development checkout only. It is not the authoritative deployment: the live Pavol-Brain services — Control Center, projector LaunchAgents, canonical journals, and retrieval databases — run on mini-core, reached from clients through the SSH stdio MCP launcher (`scripts/run_brain_mcp_ssh.sh`). Nothing in this document changes that; the local checkout exists for development, tests, and local tooling only.

## Supported and preferred Python versions

`requires-python = ">=3.11"` in `pyproject.toml`. Python 3.13.13 is the preferred local development runtime; `.python-version` pins it for uv. Python 3.11.x remains supported because it is what mini-core actually runs — do not narrow `requires-python` or introduce 3.13-only syntax without first validating on 3.11, since that would silently break the live deployment's own environment story even though this checkout isn't it.

## Environment setup

Requires `uv` (any reasonably current version; validated with uv 0.11.x) and no other manual Python install — `uv` provisions the pinned interpreter itself.

```sh
uv venv .venv --python 3.13
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

## Rollback to Python 3.11

If the 3.13 `.venv` ever needs to be rolled back locally:

```sh
mv .venv .venv-py313-failed   # keep for diagnosis unless it holds unsafe state
mv .venv-py311-backup .venv   # restore the preserved 3.11 environment, if still present
.venv/bin/python --version    # expect 3.11.15
uv run pytest tests/          # confirm the baseline still passes
```

If `.venv-py311-backup` is no longer present, rebuild it directly: `uv venv .venv --python 3.11 && uv sync --locked --group dev`.
