import math
import struct
import urllib.request
import json

from .errors import ProjectorError


def pack(vector): return struct.pack(f"<{len(vector)}f", *vector)


def valid(vector, dimension):
    if not vector or len(vector) != dimension or any(not math.isfinite(float(x)) for x in vector):
        raise ProjectorError("invalid_embedding_vector")
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm < 1e-12: raise ProjectorError("near_zero_embedding_vector")
    return [float(x) / norm for x in vector]


class HttpDocumentEmbedder:
    def __init__(self, base_url, model, dimension, timeout=60):
        self.base_url, self.model, self.dimension, self.timeout = base_url.rstrip("/"), model, dimension, timeout

    def embed_document(self, text):
        request = urllib.request.Request(self.base_url + "/embeddings", data=json.dumps({"model": self.model, "input": "search_document: " + text}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read())
        return valid(payload["data"][0]["embedding"], self.dimension), payload.get("model", self.model)
