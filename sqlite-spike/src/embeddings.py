"""OpenAI-compatible local embedding client and deterministic vector storage."""
import hashlib, json, math, os, struct, urllib.request
from datetime import datetime, timezone

DOC_PREFIX='search_document: '
QUERY_PREFIX='search_query: '

class EmbeddingError(RuntimeError): pass

class EmbeddingClient:
    def __init__(self, base_url=None, model=None, dimension=None, api_key=None):
        self.base_url=(base_url or os.getenv('EMBEDDING_BASE_URL','http://localhost:11434/v1')).rstrip('/')
        self.model=model or os.getenv('EMBEDDING_MODEL','nomic-embed-text')
        value=dimension if dimension is not None else os.getenv('EMBEDDING_DIMENSION')
        self.dimension=int(value) if value else None
        self.api_key=api_key if api_key is not None else os.getenv('EMBEDDING_API_KEY','ollama')
        self.profile=os.getenv('EMBEDDING_PROFILE','local')
        self.normalization='l2_normalized_float32'
    @property
    def fingerprint(self):
        raw={'profile':self.profile,'base_url':self.base_url,'model':self.model,'dimension':self.dimension,'document_prefix':DOC_PREFIX,'query_prefix':QUERY_PREFIX,'normalization':self.normalization}
        return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
    def contract(self): return {'profile':self.profile,'base_url':self.base_url,'model':self.model,'dimension':self.dimension,'normalization':self.normalization,'document_prefix':DOC_PREFIX,'query_prefix':QUERY_PREFIX,'fingerprint':self.fingerprint}
    def embed(self,text,kind):
        prefix=DOC_PREFIX if kind=='document' else QUERY_PREFIX
        body=json.dumps({'model':self.model,'input':prefix+text}).encode()
        request=urllib.request.Request(self.base_url+'/embeddings',data=body,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key})
        try:
            with urllib.request.urlopen(request,timeout=60) as response: payload=json.loads(response.read())
        except Exception as exc: raise EmbeddingError(f'embedding_endpoint_unavailable: {exc}') from exc
        try: vector=payload['data'][0]['embedding']
        except (KeyError,IndexError,TypeError) as exc: raise EmbeddingError('embedding_response_missing_vector') from exc
        exact_model=payload.get('model',self.model)
        return normalize(vector,self.dimension),exact_model

def normalize(vector, expected_dimension=None):
    if not vector: raise EmbeddingError('embedding_empty_vector')
    if expected_dimension is not None and len(vector)!=expected_dimension: raise EmbeddingError(f'embedding_dimension_mismatch: expected {expected_dimension}, got {len(vector)}')
    if any(not math.isfinite(float(x)) for x in vector): raise EmbeddingError('embedding_non_finite_value')
    norm=math.sqrt(sum(float(x)*float(x) for x in vector))
    if norm<1e-12: raise EmbeddingError('embedding_near_zero_norm')
    return [float(x)/norm for x in vector]
def pack(vector): return struct.pack('<%sf'%len(vector),*vector)
def unpack(blob,dimension): return list(struct.unpack('<%sf'%dimension,blob))
def cosine(left,right): return sum(a*b for a,b in zip(left,right))

def populate(con, client):
    rows=con.execute('SELECT record_id,canonical_text,projection_hash FROM retrieval_documents ORDER BY record_id').fetchall(); made=reused=0; exact_model=client.model
    for row in rows:
        cached=con.execute('SELECT projection_hash,model_fingerprint FROM retrieval_embeddings WHERE record_id=?',(row['record_id'],)).fetchone()
        if cached and cached['projection_hash']==row['projection_hash'] and cached['model_fingerprint']==client.fingerprint: reused+=1; continue
        vector,exact_model=client.embed(row['canonical_text'],'document')
        if client.dimension is None: client.dimension=len(vector)
        if len(vector)!=client.dimension: raise EmbeddingError('embedding_dimension_changed_during_build')
        con.execute('INSERT OR REPLACE INTO retrieval_embeddings VALUES (?,?,?,?,?,?,?,?)',(row['record_id'],client.fingerprint,exact_model,len(vector),pack(vector),1.0,row['projection_hash'],datetime.now(timezone.utc).isoformat())); made+=1
    con.execute('INSERT OR REPLACE INTO retrieval_embedding_meta VALUES (?,?)',('contract',json.dumps(client.contract()|{'exact_model_identifier':exact_model},sort_keys=True)))
    con.commit(); return {'embedded':made,'reused':reused,'coverage':len(rows),'contract':client.contract()|{'exact_model_identifier':exact_model}}
