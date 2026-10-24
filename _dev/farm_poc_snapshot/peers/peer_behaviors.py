from ..fastcopy import json_copy
from .peer_scripts import PeerResponseScript

class PeerBehavior:
    """Scripted endpoint: Matrix supplies telemetry; the endpoint only echoes it."""
    def __init__(self,device_id,response_mode="normal",initial_telemetry=None,initial_tick=0):
        self.device_id=device_id
        if isinstance(response_mode,dict):
            self.response_mode=response_mode.get("mode","normal")
            self.delay_ticks=max(0,int(response_mode.get("delay_ticks",0)))
        else:self.response_mode=response_mode;self.delay_ticks=0
        self.cached_telemetry=json_copy(initial_telemetry or {});self.cached_tick=initial_tick
        self.stale_telemetry=json_copy(initial_telemetry or {});self.stale_tick=initial_tick
        self._has_stale_sample=bool(initial_telemetry)
        self.script=PeerResponseScript(self.response_mode,self.delay_ticks)
    def receive(self,message,telemetry,tick):
        if message.message_type == "device_telemetry":
            # Transport payloads are already isolated copies that nobody mutates.
            self.cached_telemetry=message.payload.get("telemetry",{})
            self.cached_tick=int(message.payload.get("tick",tick))
            if not self._has_stale_sample:
                self.stale_telemetry=json_copy(self.cached_telemetry)
                self.stale_tick=self.cached_tick
                self._has_stale_sample=True
            return None
        if message.message_type!="peer_query":return None
        if self.response_mode=="stale":
            return self.script.respond(self.device_id,self.stale_telemetry,tick,
                                       self.stale_telemetry,self.stale_tick)
        return self.script.respond(self.device_id,self.cached_telemetry,tick,
                                   self.stale_telemetry,self.stale_tick,sample_tick=self.cached_tick)
