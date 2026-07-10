import importlib.util
import json
from pathlib import Path
import unittest

MODULE = Path(__file__).parents[1] / "scripts" / "validate_benchmark.py"
spec = importlib.util.spec_from_file_location("validate_benchmark", MODULE)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def record(record_id="rec-001", workspace="a", record_type="fact", status="accepted", projection=True, sensitivity="normal"):
    return {"record_id": record_id, "workspace": workspace, "type": record_type, "sensitivity": sensitivity, "valid_at": "2026-01-01T00:00:00+00:00", "expected": {"status": status, "projection": projection}}


def query(query_id="Q01", record_id="rec-001", scope=None, filters=None):
    return {"id": query_id, "query": "test", "scope": scope or ["a"], "filters": filters or {"mode": "current", "types": ["fact"], "sensitive_allowed": False}, "expected_top": [record_id], "failure_condition": "no leak"}


class BenchmarkValidatorTests(unittest.TestCase):
    def categories(self, queries, records):
        return {x["category"] for x in validator.validate_manifest(queries, records)}

    def test_workspace_mismatch(self): self.assertIn("workspace_mismatch", self.categories([query(scope=["b"])], {"rec-001": record()}))
    def test_missing_record(self): self.assertIn("missing_record", self.categories([query(record_id="missing")], {}))
    def test_ineligible_status(self): self.assertIn("ineligible_record", self.categories([query()], {"rec-001": record(status="candidate", projection=False)}))
    def test_type_mismatch(self): self.assertIn("type_mismatch", self.categories([query(filters={"mode": "current", "types": ["decision"]})], {"rec-001": record()}))
    def test_sensitive_leak(self): self.assertIn("sensitive_leak", self.categories([query()], {"rec-001": record(sensitivity="sensitive")}))
    def test_duplicate_query_id(self): self.assertIn("duplicate_query_id", self.categories([query(), query()], {"rec-001": record()}))
    def test_exact_preservation_of_24_ids(self):
        records = {f"rec-{i:03d}": record(f"rec-{i:03d}") for i in range(24)}
        source = [query(f"Q{i:02d}", f"rec-{i:03d}") for i in range(24)]
        corrected, _, _ = validator.run_preflight(source, records)
        self.assertEqual([q["id"] for q in corrected], [q["id"] for q in source])
    def test_complete_diff_audit(self):
        source = [query(scope=["wrong"], filters=None) for _ in range(24)]
        for i, item in enumerate(source): item["id"] = f"Q{i:02d}"
        corrected, changes, _ = validator.run_preflight(source, {"rec-001": record()})
        changed_ids = {item["query_id"] for item in changes}
        self.assertEqual(changed_ids, {q["id"] for q in corrected})
        self.assertTrue(all({"query_id", "field", "old_value", "new_value", "reason", "evidence_record_id"} <= set(item) for item in changes))
