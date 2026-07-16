import json,struct,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from brain.api import Brain
from brain.config import BrainConfig
from brain.errors import BrainError
from brain.repository import Repository
from brain.schemas import check_exported

ROOT=Path(__file__).parents[1]
REPORT=json.loads((ROOT/"sqlite-spike/results/vector-baseline.json").read_text())

class FixtureTransport:
 def __init__(self,lookup): self.lookup=lookup;self.calls=0
 def embed(self,text): self.calls+=1;return self.lookup[text]
class FixtureRepository:
 def __init__(self):
  self.meta={"build_id":"fixture-build","embedding_model":"fixture-fingerprint"};self.by_query={q["query"]:q for q in REPORT["queries"]}
  self.dimension=len(self.by_query);self.keys=list(self.by_query)
  self.docs={}
  for qi,q in enumerate(REPORT["queries"]):
   for result in q["returned"]:
    doc=self.docs.setdefault(result["record_id"],{"record_id":result["record_id"],"workspace":result["workspace"],"type":result["type"],"sensitivity":result["sensitivity"],"status":result["status"],"valid_at":result["valid_at"],"invalid_at":result["invalid_at"],"title":result["record_id"],"projection_hash":result["provenance"]["projection_hash"],"scores":[0.0]*self.dimension})
    # Preserve the recorded ordering even where the JSON report rounds equal
    # cosine scores; live baseline vectors retain the unrounded distinction.
    doc["scores"][qi]=result["vector_score"]+(100-result["rank"])*1e-5
  self.journal_rows={rid:{"record_id":rid,"type":d["type"],"workspace":d["workspace"],"sensitivity":d["sensitivity"],"payload":json.dumps({"fixture":rid}),"status":d["status"],"valid_at":d["valid_at"],"invalid_at":d["invalid_at"],"supersedes":None,"superseded_by":None,"updated_event_id":"evt-"+rid} for rid,d in self.docs.items()}
 def workspaces(self): return {d["workspace"] for d in self.docs.values()}
 def sensitive_workspaces(self,workspaces): return {d["workspace"] for d in self.docs.values() if d["workspace"] in workspaces and d["sensitivity"]=="sensitive"}
 def candidates(self,request):
  qi=self.keys.index(request.query);out=[]
  for d in self.docs.values():
   if d["workspace"] not in request.workspaces or d["type"] not in (request.types or []): continue
   if not request.sensitive_allowed and d["sensitivity"]=="sensitive": continue
   out.append({**d,"dimensions":self.dimension,"vector":struct.pack("<%sf"%self.dimension,*d["scores"])})
  return out
 def journal_row(self,rid): return self.journal_rows.get(rid)
 def related(self,rid): return [{"relation":"touches","record_id":"linked"}] if rid=="rec-001" else []

class BrainContractTests(unittest.TestCase):
 def setUp(self):
  self.repo=FixtureRepository();vectors={q:self._vector(i) for i,q in enumerate(self.repo.keys)};self.transport=FixtureTransport(vectors)
  self.brain=Brain(BrainConfig(embedding_dimension=self.repo.dimension),self.transport,self.repo)
 def _vector(self,i): return [1.0 if n==i else 0.0 for n in range(self.repo.dimension)]
 def request(self,q): return dict(query=q["query"],workspaces=q["scope"],types=q["filters"]["types"],mode=q["filters"]["mode"],sensitive_allowed=q["filters"]["sensitive_allowed"],limit=3)
 def test_parity_all_24_queries(self):
  for q in REPORT["queries"]:
   got=self.brain.search(**self.request(q))
   self.assertEqual([x.record_id for x in got.results],[x["record_id"] for x in q["returned"][:3]],q["query_id"])
 def test_determinism_and_provenance(self):
  q=REPORT["queries"][0];a=self.brain.search(**self.request(q));b=self.brain.search(**self.request(q));self.assertEqual(a.results,b.results);self.assertTrue(all(x.provenance.source_event_id for x in a.results))
 def test_invalid_request_does_not_embed(self):
  with self.assertRaises(BrainError) as err:self.brain.search(query=" ",workspaces=["ai-pos"])
  self.assertEqual(err.exception.code,"BRAIN_EMPTY_QUERY");self.assertEqual(self.transport.calls,0)
 def test_request_id_contract(self):
  q=REPORT["queries"][0]; supplied=self.brain.search(**self.request(q),request_id="caller-id");self.assertEqual(supplied.request_id,"caller-id")
  automatic=self.brain.search(**self.request(q));self.assertTrue(automatic.request_id.startswith("uuid4-compat:"))
 def test_missing_database_does_not_create_file(self):
  path=Path(tempfile.mkdtemp())/"missing.db";repo=Repository(BrainConfig(retrieval_db_path=path,journal_db_path=path))
  with self.assertRaises(BrainError) as err:
   with repo.retrieval():pass
  self.assertEqual(err.exception.code,"BRAIN_INDEX_UNAVAILABLE");self.assertFalse(path.exists())
 def test_min_score_disabled(self):
  q=REPORT["queries"][0]
  with self.assertRaises(BrainError) as err:self.brain.search(**self.request(q),min_score=.5)
  self.assertEqual(err.exception.code,"BRAIN_FEATURE_NOT_ENABLED")
 def test_unknown_and_sensitive_scope(self):
  q=REPORT["queries"][0]
  with self.assertRaises(BrainError) as err:self.brain.search(**{**self.request(q),"workspaces":["missing"]})
  self.assertEqual(err.exception.code,"BRAIN_UNKNOWN_WORKSPACE")
  q=next(x for x in REPORT["queries"] if x["query_id"]=="Q22")
  with self.assertRaises(BrainError) as err:self.brain.search(**{**self.request(q),"sensitive_allowed":False})
  self.assertEqual(err.exception.code,"BRAIN_SENSITIVE_SCOPE_DENIED")
 def test_record_related_and_as_of_policy(self):
  record=self.brain.get_record("rec-001",allowed_workspaces=["ai-pos"]);self.assertEqual(record.record_id,"rec-001")
  self.assertEqual(self.brain.get_related("rec-001",allowed_workspaces=["ai-pos"]).related[0]["relation"],"touches")
  q=REPORT["queries"][0]
  with self.assertRaises(BrainError) as err:self.brain.search(**{**self.request(q),"as_of":"bad"})
  self.assertEqual(err.exception.code,"BRAIN_INVALID_AS_OF")
 def test_schemas_are_exported_and_not_drifted(self): self.assertTrue(check_exported())
