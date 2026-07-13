#!/usr/bin/env python3
"""Operator-only bounded projector; journal writes use a separate contract."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain.config import BrainConfig
from brain.projector import ProjectorConfig, ProjectionProjector
from brain.projector.embedding_cache import HttpDocumentEmbedder
from brain.projector.journal_reader import JournalReader, sha256


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--journal-db", type=Path, required=True); p.add_argument("--retrieval-db", type=Path, required=True)
    p.add_argument("--batch-size", type=int, default=100); p.add_argument("--plan", action="store_true"); p.add_argument("--run-once", action="store_true"); p.add_argument("--validate", action="store_true"); p.add_argument("--output", type=Path)
    p.add_argument("--embedding-base-url", default="http://localhost:11434/v1"); p.add_argument("--embedding-model", default="nomic-embed-text:latest"); p.add_argument("--embedding-dimension", type=int, default=768)
    a = p.parse_args()
    if not (a.plan or a.run_once or a.validate): a.plan = True
    fp = hashlib.sha256(json.dumps({"profile": "local", "base_url": a.embedding_base_url.rstrip("/"), "model": a.embedding_model, "dimension": a.embedding_dimension, "document_prefix": "search_document: ", "query_prefix": "search_query: ", "normalization": "l2_normalized_float32"}, sort_keys=True).encode()).hexdigest()
    config = ProjectorConfig(a.journal_db, a.retrieval_db, fp, a.embedding_dimension, a.embedding_model)
    projector = ProjectionProjector(config, HttpDocumentEmbedder(a.embedding_base_url, a.embedding_model, a.embedding_dimension))
    journal_hash_before = sha256(a.journal_db)
    output = {"journal_path": str(a.journal_db), "retrieval_path": str(a.retrieval_db), "journal_hash_before": journal_hash_before, "journal_schema_audit": JournalReader(a.journal_db).audit()}
    if a.plan: output["plan"] = projector.plan(a.batch_size).as_dict()
    if a.run_once: output["run"] = projector.run_once(a.batch_size).as_dict()
    if a.validate: output["validation"] = projector.validate()
    output["journal_hash_after"] = sha256(a.journal_db); output["journal_unchanged"] = output["journal_hash_before"] == output["journal_hash_after"]
    text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    if a.output:
        a.output.write_text(text + "\n")
        a.output.with_name("slice2-journal-schema-audit.json").write_text(json.dumps(output["journal_schema_audit"], ensure_ascii=False, indent=2) + "\n")
    print(text)

if __name__ == "__main__": main()
