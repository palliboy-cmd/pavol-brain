from .api import Brain
from .config import BrainConfig
from .errors import BrainError

_default=Brain()
def search(**kwargs): return _default.search(**kwargs)
def get_record(record_id,**kwargs): return _default.get_record(record_id,**kwargs)
def get_related(record_id,**kwargs): return _default.get_related(record_id,**kwargs)
def health(): return _default.health()
def rebuild_status(): return _default.rebuild_status()
def record_outcome(**kwargs): return _default.record_outcome(**kwargs)
def record_decision(**kwargs): return _default.record_decision(**kwargs)
def record_problem(**kwargs): return _default.record_problem(**kwargs)
def record_analysis(**kwargs): return _default.record_analysis(**kwargs)
