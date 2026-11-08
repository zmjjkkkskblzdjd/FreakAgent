import json
import os
from dataclasses import asdict, dataclass


@dataclass
class TraceEvent:

    session_id: str          
    seq: int                 
    timestamp: str           
    event_type: str          
    action: str              
    actor: str               
    ok: bool                 
    error: str = ""          
    duration_ms: int = 0     
    args_digest: str = ""   
    tier: str = ""           


class Tracer:

    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def emit(self, event: TraceEvent) -> None:
        path = os.path.join(self.log_dir, f"trace_{event.session_id}.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except OSError:
            pass  
