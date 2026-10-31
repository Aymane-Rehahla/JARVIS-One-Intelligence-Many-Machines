from dataclasses import dataclass, field
from ..fastcopy import json_copy

@dataclass
class BaseDevice:
    device_id: str
    config: dict = field(default_factory=dict)
    internal: dict = field(default_factory=dict)
    telemetry: dict = field(default_factory=dict)
    faults: list = field(default_factory=list)
    online: bool = True
    telemetry_history: list = field(default_factory=list)
    def public_telemetry(self): return json_copy(self.telemetry)
    def monitoring_output(self, copy=True): return self.public_telemetry() if copy else self.telemetry
    def update(self, dt: float): raise NotImplementedError
    def record_telemetry(self, tick, time_s, limit=64):
        if "last_telemetry_tick" in self.telemetry:
            self.telemetry["last_telemetry_tick"]=int(tick)
        record={"tick":int(tick),"time_s":float(time_s),"telemetry":self.public_telemetry()}
        self.telemetry_history.append(record)
        del self.telemetry_history[:-limit]
        # History records are immutable once stored; callers that keep one must copy it.
