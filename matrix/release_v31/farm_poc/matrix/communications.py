"""Simulated phone service: the farm owner and the emergency services.

Nothing here contacts anyone real. Calls and notifications run on simulation
time and have consequences inside the Matrix:

- The simulated owner answers after a delay that depends on the time of day,
  and in some episodes cannot be reached at all.
- The fire brigade takes the call, drives out, and on arrival suppresses an
  active fire. A call without a fire is recorded as a false alarm.
- Civil protection (floods, storm damage) arrives later and pumps water out of
  the barn. Without a flood or storm emergency the call is a false alarm.

Duplicate reports of the same incident are merged the way a dispatcher would,
and the caller is told who reported it first. The full log is evaluator-only;
callers see the status of their own calls.
"""
from __future__ import annotations

import random

SERVICES = ("fire_brigade", "civil_protection")
DAY_S = 86_400.0


class CommunicationsService:
    def __init__(self, engine, seed, config=None):
        config = dict(config or {})
        self.engine = engine
        self.rng = random.Random(f"testbed-comms-{seed}")
        self.owner_unreachable = self.rng.random() < float(config.get("owner_unreachable_probability", 0.10))
        self.day_delay_s = tuple(config.get("owner_day_delay_s", (60.0, 300.0)))
        self.night_delay_s = tuple(config.get("owner_night_delay_s", (300.0, 1800.0)))
        self.fire_travel_s = tuple(config.get("fire_brigade_travel_s", (720.0, 1200.0)))
        self.civil_travel_s = tuple(config.get("civil_protection_travel_s", (1800.0, 3600.0)))
        self.dispatch_handling_s = float(config.get("dispatch_handling_s", 60.0))
        self.notification_merge_s = float(config.get("notification_merge_s", 1800.0))
        self.call_merge_s = float(config.get("call_merge_s", 7200.0))
        self.notifications: list[dict] = []
        self.calls: list[dict] = []
        self.log: list[dict] = []  # evaluator-only

    # ------------------------------------------------------------- clock
    def _now(self):
        return float(self.engine.clock.time)

    def hour_of_day(self):
        weather = getattr(self.engine, "weather", None)
        if weather is not None:
            return weather.hour_of_day(self._now())
        return (6.0 + self._now() / 3600.0) % 24.0

    def _owner_delay(self):
        night = self.hour_of_day() >= 22.0 or self.hour_of_day() < 6.0
        low, high = self.night_delay_s if night else self.day_delay_s
        return self.rng.uniform(float(low), float(high))

    # ------------------------------------------------------------- owner
    def notify_owner(self, *, sender, incident_id, priority, summary):
        """Push notification / phone call to the farm owner."""
        if priority not in ("info", "warning", "urgent", "emergency"):
            raise ValueError("priority must be info, warning, urgent, or emergency")
        now = self._now()
        for record in self.notifications:
            if record["incident_id"] == incident_id and now - record["sent_s"] < self.notification_merge_s \
                    and _rank(priority) <= _rank(record["priority"]):
                return {**record, "merged": True}
        record = {"notification_id": f"note-{len(self.notifications) + 1:04d}", "sender": sender,
                  "incident_id": incident_id, "priority": priority, "summary": str(summary)[:500],
                  "sent_s": now, "status": "delivered",
                  "acknowledged_s": None if self.owner_unreachable else now + self._owner_delay()}
        self.notifications.append(record)
        self.log.append({"time_s": now, "event": "owner_notified", **record})
        self.engine.emit("operator_notified", sender, notification_id=record["notification_id"],
                         priority=priority, incident_id=incident_id)
        return dict(record)

    # ------------------------------------------------------------- emergency services
    def call_emergency(self, *, service, sender, incident_id, nature, location=None):
        if service not in SERVICES:
            raise ValueError(f"unknown emergency service: {service}")
        now = self._now()
        for record in self.calls:
            if record["service"] == service and now - record["called_s"] < self.call_merge_s \
                    and record["status"] not in ("cleared", "false_alarm"):
                record["duplicate_reports"].append({"sender": sender, "time_s": now, "incident_id": incident_id})
                self.log.append({"time_s": now, "event": "duplicate_report", "call_id": record["call_id"], "sender": sender})
                return {**self._public(record), "merged": True, "already_reported_by": record["sender"]}
        travel = self.fire_travel_s if service == "fire_brigade" else self.civil_travel_s
        record = {"call_id": f"call-{len(self.calls) + 1:04d}", "service": service, "sender": sender,
                  "incident_id": incident_id, "nature": str(nature)[:200], "location": location,
                  "called_s": now, "status": "received",
                  "dispatched_s": now + self.dispatch_handling_s,
                  "arrival_s": now + self.dispatch_handling_s + self.rng.uniform(float(travel[0]), float(travel[1])),
                  "outcome": None, "duplicate_reports": []}
        self.calls.append(record)
        self.log.append({"time_s": now, "event": "emergency_call", **{k: v for k, v in record.items() if k != "duplicate_reports"}})
        self.engine.emit("emergency_call_placed", sender, call_id=record["call_id"], service=service, incident_id=incident_id)
        return self._public(record)

    # ------------------------------------------------------------- simulation
    def update(self):
        now = self._now()
        for record in self.notifications:
            if record["status"] == "delivered" and record["acknowledged_s"] is not None and now >= record["acknowledged_s"]:
                record["status"] = "acknowledged"
                self.log.append({"time_s": now, "event": "owner_acknowledged", "notification_id": record["notification_id"]})
        for record in self.calls:
            if record["status"] == "received" and now >= record["dispatched_s"]:
                record["status"] = "dispatched"
            if record["status"] == "dispatched" and now >= record["arrival_s"]:
                self._arrive(record, now)

    def _arrive(self, record, now):
        world = self.engine.physical_world
        if record["service"] == "fire_brigade":
            fire = world.fire
            if fire is not None and fire.state in ("burning", "burnout"):
                world.extinguish()
                record["status"], record["outcome"] = "on_scene", "fire_suppressed"
            else:
                record["status"], record["outcome"] = "false_alarm", "no_fire_found"
        else:
            flooded = world.state.get("barn_floor_water_cm", 0.0) > 0.0 or world.state["outside"]["floodwater_level_cm"] >= 10.0
            storm = bool(world.state.get("storm", {}).get("active"))
            if flooded or storm:
                world.state["barn_pumping"] = flooded
                record["status"], record["outcome"] = "on_scene", "pumping_water" if flooded else "securing_site"
            else:
                record["status"], record["outcome"] = "false_alarm", "no_emergency_found"
        self.log.append({"time_s": now, "event": "responders_arrived", "call_id": record["call_id"],
                         "outcome": record["outcome"]})
        self.engine.emit("responders_arrived", record["service"], call_id=record["call_id"], outcome=record["outcome"])

    # ------------------------------------------------------------- views
    @staticmethod
    def _public(record):
        return {key: record[key] for key in ("call_id", "service", "sender", "incident_id", "called_s", "status", "outcome")}

    def status_for(self, sender):
        """What a caller can know: its own notifications and the status of calls it made or joined."""
        notes = [{k: r[k] for k in ("notification_id", "incident_id", "priority", "sent_s", "status")}
                 for r in self.notifications if r["sender"] == sender]
        calls = [self._public(r) for r in self.calls
                 if r["sender"] == sender or any(d["sender"] == sender for d in r["duplicate_reports"])]
        return {"notifications": notes, "calls": calls}


def _rank(priority):
    return ("info", "warning", "urgent", "emergency").index(priority)
