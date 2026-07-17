#!/usr/bin/env python3
"""Thin wrapper over pytest that runs only the §10 adversarial acceptance
suite (docs/architecture/write-safety-integrity-repair-spec.md §10; row ->
test mapping in tests/ACCEPTANCE_MATRIX.md).

This is not a parallel test framework: it is `pytest tests/ -m acceptance`
plus (a) a safety net that strips the live-instance BRAIN_* environment
variables so a test that forgot to construct its own tmp_path config could
never resolve to a real journal, and (b) a small summary line parsed from
pytest's own output. Same interpreter, same tests/ tree, same collection
rules as the authoritative `pytest tests/ -q` -- this script changes nothing
about how the tests run, only which subset is selected.

Usage:
    scripts/run_brain_acceptance.py [pytest-args...]

Exit codes: passed straight through from pytest (0 = all selected tests
passed, non-zero on any failure/error). `-m acceptance` selects only tests
whose module carries `pytestmark = pytest.mark.acceptance` -- currently
tests/test_brain_instance_bootstrap.py, tests/test_brain_write.py,
tests/test_brain_scope_integrity.py, tests/test_artifact_validation.py,
tests/test_brain_projector.py, tests/test_brain_control.py. Legacy/spike
suites under spike/ and sqlite-spike/ are outside tests/ and were never
collected by `pytest tests/` in the first place.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_ENV_VARS = (
    "BRAIN_STATE_DIR",
    "BRAIN_PERSONAL_JOURNAL_DB", "BRAIN_PERSONAL_RETRIEVAL_DB",
    "BRAIN_WORK_JOURNAL_DB", "BRAIN_WORK_RETRIEVAL_DB",
    "BRAIN_JOURNAL_DB", "BRAIN_RETRIEVAL_DB",
    "BRAIN_AUDIT_LOG",
)
# pytest's final summary line is a comma-separated list of "<n> <label>"
# segments in no fixed order/count, e.g. "230 passed, 68 deselected,
# 21 subtests passed in 6.80s" -- match each segment independently.
SEGMENT_RE = re.compile(r"(\d+) (passed|subtests passed|failed|errors?|skipped|deselected)")


def main(argv):
    env = {k: v for k, v in os.environ.items() if k not in LIVE_ENV_VARS}
    cmd = [sys.executable, "-m", "pytest", "tests/", "-m", "acceptance", "-q", *argv]
    result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)

    counts = {"passed": 0, "subtests passed": 0, "failed": 0, "errors": 0, "skipped": 0, "deselected": 0}
    for line in (result.stdout + result.stderr).splitlines():
        for count, label in SEGMENT_RE.findall(line):
            key = "errors" if label.startswith("error") else label
            counts[key] = int(count)
    print(
        f"\nacceptance suite: {counts['passed']} passed, {counts['subtests passed']} subtests passed, "
        f"{counts['failed']} failed, {counts['errors']} errors, {counts['skipped']} skipped, "
        f"{counts['deselected']} deselected (non-acceptance) "
        f"(exit {result.returncode})"
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
