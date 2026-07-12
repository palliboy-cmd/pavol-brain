import json
from pathlib import Path
from .models import SearchRequest,SearchResponse,RecordEnvelope,RelatedResponse,HealthReport,RebuildStatus,BrainErrorModel

MODELS={"SearchRequest":SearchRequest,"SearchResponse":SearchResponse,"RecordEnvelope":RecordEnvelope,"RelatedResponse":RelatedResponse,"HealthReport":HealthReport,"RebuildStatus":RebuildStatus,"BrainError":BrainErrorModel}
ROOT=Path(__file__).parent/"schemas"/"v1"
def generated(): return {name:model.model_json_schema() for name,model in MODELS.items()}
def export():
    ROOT.mkdir(parents=True,exist_ok=True)
    for name,schema in generated().items(): (ROOT/f"{name}.json").write_text(json.dumps(schema,sort_keys=True,indent=2)+"\n")
def check_exported():
    return all((ROOT/f"{name}.json").exists() and json.loads((ROOT/f"{name}.json").read_text())==schema for name,schema in generated().items())
