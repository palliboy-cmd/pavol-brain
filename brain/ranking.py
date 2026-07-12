import math,struct
from .errors import BrainError
def unpack(blob,dimension): return struct.unpack("<%sf"%dimension,blob)
def cosine(left,right):
    if len(left)!=len(right): raise ValueError("dimension mismatch")
    return sum(a*b for a,b in zip(left,right))
def normalize(vector):
    if not vector or any(not math.isfinite(float(x)) for x in vector):
        raise ValueError("invalid vector")
    norm=math.sqrt(sum(float(x)*float(x) for x in vector))
    if norm<1e-12: raise ValueError("near-zero vector")
    return [float(x)/norm for x in vector]
def rank(rows,query_vector):
    hits=[]
    for row in rows:
        score=cosine(query_vector,unpack(row["vector"],row["dimensions"]))
        if not math.isfinite(score): continue
        hits.append((score,row))
    # Stable passes express the required total order without score rounding:
    # score DESC, valid_at DESC, record_id ASC.
    hits.sort(key=lambda item:item[1]["record_id"])
    hits.sort(key=lambda item:item[1]["valid_at"],reverse=True)
    hits.sort(key=lambda item:item[0],reverse=True)
    return hits
