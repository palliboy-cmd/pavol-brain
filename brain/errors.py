from dataclasses import dataclass, field

@dataclass
class BrainError(Exception):
    code: str
    message: str
    request_id: str
    details: dict = field(default_factory=dict)
    def __str__(self): return f"{self.code}: {self.message}"
