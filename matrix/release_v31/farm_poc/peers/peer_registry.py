from .simulated_jarvis import SimulatedJarvis
class PeerRegistry:
    def __init__(self,engine,modes=None):
        self.peers={}
        for device_id in ("incubator-2","hvac","animal-protection"):
            peer=SimulatedJarvis(device_id,(modes or {}).get(device_id,"normal"),{},engine.clock.tick); self.peers[device_id]=peer
            engine.transport.register(device_id,lambda m,p=peer,d=device_id:self._handle(m,p,d,engine))
    @staticmethod
    def _handle(message,peer,device_id,engine):
        report=peer.receive(message,{},engine.clock.tick)
        if report is not None:
            delay=report.pop("delay_ticks")*engine.clock.step_seconds
            engine.send_message(device_id,message.source_id,"peer_report",report,delay=delay,correlation_id=message.message_id)
    def query_all(self,engine):
        for device_id in self.peers:
            if device_id in engine.pending_peer_queries:continue
            message_id=engine.send_message("jarvis-real",device_id,"peer_query",{"question":"report current device observations"})
            engine.pending_peer_queries[device_id]={"message_id":message_id,"query_tick":engine.clock.tick,"deadline_tick":engine.clock.tick+engine.peer_timeout_ticks}
