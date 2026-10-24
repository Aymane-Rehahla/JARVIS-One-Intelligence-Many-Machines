"""Node memory (CMM v2): incidents, recurrence, recovery, spread, and short history.

Each node owns one instance. It stores only what the node saw or concluded:
its own monitoring data, peer messages, and its own outputs. It is the source
of the escalation context ("third time in 24 h", "not recovered after 30 min",
"another node reports the same incident") that both brains receive.
"""
from __future__ import annotations

from collections import deque

DAY_S = 86_400.0


class NodeMemory:
    RECOVERY_QUIET_S = 60.0       # NORMAL this long closes an incident
    HISTORY_PERIOD_S = 60.0       # one history sample per simulated minute
    HISTORY_SAMPLES = 120         # two hours

    def __init__(self, node_id: str):
        self.node_id = node_id
        self.incidents: dict[str, dict] = {}
        self.active_id: str | None = None
        self.history: deque = deque(maxlen=self.HISTORY_SAMPLES)
        self.last_valid_zone_temperature: dict[str, float] = {}
        self.lost_while_hot: set[str] = set()
        self._quiet_since: float | None = None
        self._last_history_s = -1e18
        self._counter = 0
        self.call_status: dict[str, str] = {}   # service -> latest status known to this node
        self.action_results: dict[str, str] = {}  # protective action -> final command status
        self.fire_resolved_s: float | None = None
        self.dry_pumping_s = 0.0                # incubator: pump running with no water coming in
        self._last_water_s: float | None = None

    def track_water_inlet(self, time_s: float, pump_on: bool, inlet_ml_min) -> None:
        """Seconds the humidifier pump has run without inflow since water last came in through the hose."""
        dt = 0.0 if self._last_water_s is None else max(0.0, time_s - self._last_water_s)
        self._last_water_s = time_s
        if inlet_ml_min is not None and inlet_ml_min >= 0.5:
            self.dry_pumping_s = 0.0
        elif pump_on and inlet_ml_min is not None:
            self.dry_pumping_s += dt

    # ------------------------------------------------------------ observation history
    def remember_readings(self, time_s: float, readings: dict) -> None:
        if time_s - self._last_history_s >= self.HISTORY_PERIOD_S:
            self.history.append((time_s, dict(readings)))
            self._last_history_s = time_s

    def change_over(self, key: str, window_s: float, time_s: float):
        """Change of a remembered reading over roughly ``window_s`` (None if unknown)."""
        past = [(t, r.get(key)) for t, r in self.history if time_s - t >= window_s and r.get(key) is not None]
        now = [r.get(key) for t, r in self.history if r.get(key) is not None]
        if not past or not now:
            return None
        return float(now[-1]) - float(past[-1][1])

    def sustained(self, key: str, threshold: float, window_s: float, time_s: float, above: bool = True) -> bool:
        """True when every remembered sample of ``key`` in the window is above (or below) ``threshold``."""
        rows = [r.get(key) for t, r in self.history if time_s - t <= window_s]
        if len(rows) < max(2, int(window_s / self.HISTORY_PERIOD_S) - 1) or any(v is None for v in rows):
            return False
        return all((v >= threshold) if above else (v <= threshold) for v in rows)

    def track_zone_sensors(self, zone_temperatures: dict) -> None:
        """Remember zones whose sensor went silent after reading hot (heat-damaged sensors)."""
        for zone, value in (zone_temperatures or {}).items():
            if value is None:
                if self.last_valid_zone_temperature.get(zone, 0.0) >= 30.0:
                    self.lost_while_hot.add(zone)
            else:
                self.last_valid_zone_temperature[zone] = float(value)

    def sensor_lost_while_hot(self) -> bool:
        # Sensors destroyed by a fire the responders have already dealt with are known damage, not new evidence.
        return bool(self.lost_while_hot) and self.fire_resolved_s is None

    def note_call_status(self, time_s: float, statuses: dict[str, str]) -> None:
        """Statuses of emergency calls this node made or heard about from peers."""
        order = ("received", "dispatched", "on_scene", "cleared", "false_alarm")
        for service, status in statuses.items():
            known = self.call_status.get(service)
            if known is None or order.index(status) >= order.index(known):
                self.call_status[service] = status
        if self.call_status.get("fire_brigade") == "on_scene" and self.fire_resolved_s is None:
            self.fire_resolved_s = time_s

    def call_active(self, service: str) -> bool:
        return self.call_status.get(service) in ("received", "dispatched", "on_scene")

    # ------------------------------------------------------------ incidents
    def adopt_or_open(self, kind: str, diagnosis: str, time_s: float, peer_incident_id: str | None = None) -> str:
        """Join the peers' incident of the same kind, continue our own, or open a new one."""
        if self.active_id and self.incidents[self.active_id]["kind"] == kind and self.incidents[self.active_id]["recovered_s"] is None:
            incident = self.incidents[self.active_id]
        else:
            if peer_incident_id and peer_incident_id in self.incidents:
                incident = self.incidents[peer_incident_id]
                incident["recovered_s"] = None
            else:
                self._counter += 1
                incident_id = peer_incident_id or f"{self.node_id}-{kind}-{self._counter:04d}"
                incident = {"incident_id": incident_id, "kind": kind, "diagnosis": diagnosis, "opened_s": time_s,
                            "last_seen_s": time_s, "recovered_s": None, "max_assessment": "NORMAL",
                            "handled_by_machine": False, "peers": set(), "notified": False, "calls": set(),
                            "actions": [], "notified_priority": None, "notified_s": None}
                self.incidents[incident["incident_id"]] = incident
            self.active_id = incident["incident_id"]
        incident["diagnosis"] = diagnosis
        incident["last_seen_s"] = time_s
        return incident["incident_id"]

    _now = 0.0

    def record(self, time_s: float, assessment: str, diagnosis: str, kind: str | None,
               peer_reports: dict[str, tuple[str, str | None]] | None = None) -> str | None:
        """Update incidents from this tick's conclusion; returns the active incident id."""
        from .contracts import ASSESSMENTS
        self._now = time_s
        peer_reports = peer_reports or {}
        if assessment == "NORMAL" or kind is None:
            if self.active_id and self._quiet_since is None:
                self._quiet_since = time_s
            if self.active_id and self._quiet_since is not None and time_s - self._quiet_since >= self.RECOVERY_QUIET_S:
                self.incidents[self.active_id]["recovered_s"] = time_s
                self.active_id = None
                self._quiet_since = None
            return self.active_id
        self._quiet_since = None
        peer_id = next((incident_id for (peer_kind, incident_id) in peer_reports.values()
                        if peer_kind == kind and incident_id), None)
        incident_id = self.adopt_or_open(kind, diagnosis, time_s, peer_id)
        incident = self.incidents[incident_id]
        if ASSESSMENTS.index(assessment) > ASSESSMENTS.index(incident["max_assessment"]):
            incident["max_assessment"] = assessment
        incident["handled_by_machine"] = incident["handled_by_machine"] or assessment == "HANDLED_BY_MACHINE"
        incident["peers"].update(sender for sender, (peer_kind, _) in peer_reports.items() if peer_kind == kind)
        return incident_id

    def active(self) -> dict | None:
        return self.incidents.get(self.active_id) if self.active_id else None

    def recurrence_24h(self, kind: str, diagnosis: str | None, time_s: float) -> int:
        return sum(1 for incident in self.incidents.values()
                   if incident["kind"] == kind and (diagnosis is None or incident["diagnosis"] == diagnosis)
                   and time_s - incident["opened_s"] <= DAY_S)

    def mark(self, *, notified=False, call=None, action=None, priority=None):
        incident = self.active()
        if incident is None:
            return
        if notified and incident["notified_s"] is None:
            incident["notified_s"] = self._now
        incident["notified"] = incident["notified"] or notified
        if priority: incident["notified_priority"] = priority
        if call: incident["calls"].add(call)
        if action: incident["actions"].append(action)

    # ------------------------------------------------------------ brain context
    CONTEXT_NAMES = ("incident_active", "incident_age_h", "recurrence_24h", "handled_by_machine", "peers_same_incident",
                     "owner_notified", "owner_acknowledged", "fire_brigade_called", "civil_protection_called",
                     "protective_action_taken", "recovered_recently", "sensor_lost_while_hot",
                     "minutes_since_owner_notified", "owner_notified_priority", "water_dry_pumping_min")

    def context(self, time_s: float, comms_status: dict | None = None) -> dict[str, float]:
        incident = self.active()
        comms_status = comms_status or {"notifications": [], "calls": []}
        incident_id = incident["incident_id"] if incident else None
        acknowledged = any(n["status"] == "acknowledged" and n["incident_id"] == incident_id for n in comms_status["notifications"])
        services = {c["service"] for c in comms_status["calls"]
                    if (incident and c["incident_id"] == incident_id) or c["sender"] == f"jarvis-{self.node_id}"}
        recent = any(i["recovered_s"] is not None and time_s - i["recovered_s"] < 3600 for i in self.incidents.values())
        return {
            "incident_active": float(incident is not None),
            "incident_age_h": min(24.0, (time_s - incident["opened_s"]) / 3600.0) if incident else 0.0,
            "recurrence_24h": float(self.recurrence_24h(incident["kind"], incident["diagnosis"], time_s)) if incident else 0.0,
            "handled_by_machine": float(bool(incident and incident["handled_by_machine"])),
            "peers_same_incident": float(len(incident["peers"])) if incident else 0.0,
            "owner_notified": float(bool(incident and incident["notified"])),
            "owner_acknowledged": float(acknowledged),
            "fire_brigade_called": float("fire_brigade" in services or self.call_status.get("fire_brigade") is not None),
            "civil_protection_called": float("civil_protection" in services or self.call_status.get("civil_protection") is not None),
            "protective_action_taken": float(bool(incident and incident["actions"])),
            "recovered_recently": float(recent),
            "sensor_lost_while_hot": float(self.sensor_lost_while_hot()),
            "minutes_since_owner_notified": min(60.0, (time_s - incident["notified_s"]) / 60.0)
            if incident and incident.get("notified_s") is not None else 0.0,
            "owner_notified_priority": float(("info", "warning", "urgent", "emergency").index(incident["notified_priority"]) + 1)
            if incident and incident.get("notified_priority") else 0.0,
            "water_dry_pumping_min": min(60.0, self.dry_pumping_s / 60.0),
        }

    def summary(self, limit: int = 10) -> list[dict]:
        rows = sorted(self.incidents.values(), key=lambda i: i["opened_s"])[-limit:]
        return [{k: (sorted(v) if isinstance(v, set) else v) for k, v in row.items()} for row in rows]
