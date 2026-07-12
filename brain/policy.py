from datetime import datetime
from .errors import BrainError

FORBIDDEN={"candidate","rejected","forgotten"}
def parse_as_of(value,request_id):
    try: return datetime.fromisoformat(value.replace("Z","+00:00"))
    except Exception: raise BrainError("BRAIN_INVALID_AS_OF","as_of must be ISO-8601",request_id)
def eligible(row,request):
    if row["status"] in FORBIDDEN: return False
    if request.mode=="current": return row["status"]=="accepted" and not row.get("invalid_at")
    if row["status"] not in {"accepted","superseded"}: return False
    if request.as_of:
        moment=parse_as_of(request.as_of,request.request_id)
        valid=datetime.fromisoformat(row["valid_at"].replace("Z","+00:00"))
        invalid=datetime.fromisoformat(row["invalid_at"].replace("Z","+00:00")) if row.get("invalid_at") else None
        return valid<=moment and (invalid is None or invalid>moment)
    return True
