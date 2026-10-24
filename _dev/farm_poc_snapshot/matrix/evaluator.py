class Evaluator:
    def __init__(self):
        self._event_cursor = 0
        self._protection_event = None
        self.events_scanned = 0

    def evaluate(self,engine):
        d=engine.world.devices["incubator-1"]; i=d.internal
        threshold=float(d.config.get("overvoltage_threshold_v",260.0)); overvoltage=float(i["ac_voltage_v"])>threshold
        fresh=[];stale=[]
        for peer_id,report in engine.peer_reports.items():
            age=engine.clock.tick-report.get("sample_tick",-10**9)
            (fresh if 0<=age<=engine.peer_freshness_ticks else stale).append((peer_id,report))
        elevated=[pid for pid,r in fresh if r.get("summary",{}).get("ac_voltage_v") is not None and r["summary"]["ac_voltage_v"]>threshold]
        normal=[pid for pid,r in fresh if r.get("summary",{}).get("ac_voltage_v") is not None and r["summary"]["ac_voltage_v"]<=threshold]
        events = engine.world.events
        if len(events) < self._event_cursor:
            # Defensive recovery if a caller explicitly truncates episode history.
            self._event_cursor = 0
            self._protection_event = None
        for event in events[self._event_cursor:]:
            self.events_scanned += 1
            if (self._protection_event is None and event["type"] == "protection_activated"
                    and event["source_id"] == "incubator-1"):
                self._protection_event = event
        self._event_cursor = len(events)
        protection_event = self._protection_event
        protected=bool(protection_event and protection_event["data"].get("heater_on") is False and protection_event["data"].get("cutoff") is True and protection_event["data"].get("heater_power_w")==0)
        corroborated=bool(elevated)
        return {"episode_id":engine.world.episode_id,"tick":engine.clock.tick,"injected":engine.hidden_truth is not None,"local_protection":protected,"local_controller_credit":protected,"ai_supervision_credit":False,"published_alarm":"OVERVOLTAGE_PROTECTION" in d.telemetry.get("alarms",[]),"peer_evidence":{"fresh_report_count":len(fresh),"stale_report_count":len(stale),"elevated_voltage_peers":elevated,"normal_voltage_peers":normal,"missing_peers":sorted(engine.peer_missing),"unavailable_voltage_peers":[pid for pid,r in fresh if "ac_voltage_v" not in r.get("summary",{})]},"sufficient_peer_evidence":corroborated,"wider_supply_evidence":corroborated,"conflicting_peer_evidence":bool((overvoltage and normal) or (elevated and normal)),"root_cause_proven":False,"command_states":{k:v["status"] for k,v in engine.world.commands.items()},"score":1.0 if protected else 0.0}
