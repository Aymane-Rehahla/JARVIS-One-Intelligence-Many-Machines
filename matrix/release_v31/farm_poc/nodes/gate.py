"""Safety gate v2: the only path from a node's conclusion to a command or a call.

Whatever a brain proposes (rule or CTM), the gate enforces:
- own machine only: a node can command nothing but the machine it supervises;
- DANGER only, from the approved list, and only when deterministic evidence
  for that hazard is present (never a single sensor);
- rate limits, and verification that each command actually took effect;
- notifications merged per incident; emergency calls only with evidence, only
  by the incident lead (or after the lead has stayed silent), and every call
  also notifies the owner;
- civil protection only after the owner has had 5 minutes to answer.
"""
from __future__ import annotations

from . import evidence
from .contracts import MACHINE_TYPES, NodeOutput

ACTION_COMMANDS = {
    "emergency_stop_heater": ("emergency_stop", {"on": True}),
    "fire_shutdown": ("set_mode", {"mode": "fire_shutdown"}),
    "emergency_ventilation": ("emergency_ventilation", {"on": True}),
    "deploy_shutters": ("deploy_shutters", {"deploy": True}),
    "deploy_flood_barrier": ("deploy_flood_barrier", {"deploy": True}),
    "activate_alarm": ("activate_alarm", {"on": True}),
}
ACTION_COOLDOWN_S = 600.0
LEAD_GRACE_S = 60.0
OWNER_GRACE_S = 300.0
OWNER_REPEAT_S = 7200.0   # do not re-notify the same node+diagnosis within two hours unless it got worse
PRIORITY = {"ESCALATE_TO_USER": "urgent", "DANGER": "emergency", "WATCHING": "info", "HANDLED_BY_MACHINE": "info"}


def jarvis_id(node_id: str) -> str:
    return f"jarvis-{node_id}"


class SafetyGateV2:
    def __init__(self, engine):
        self.engine = engine
        self.last_action_s: dict[tuple[str, str], float] = {}
        self.pending: list[dict] = []      # commands awaiting verification
        self.decisions: list[dict] = []    # audit trail of every gate decision
        self.owner_told: dict[tuple[str, str], tuple[float, str]] = {}  # (node, diagnosis) -> (time, priority)
        self._last_refusal: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------ evidence per action
    def _action_justified(self, action, ctx) -> tuple[bool, str]:
        m, machine = ctx.monitoring, MACHINE_TYPES[ctx.node_id]
        if action == "emergency_stop_heater":
            return evidence.incubator_overheating_despite_cutoffs(m), "overheating despite cutoffs"
        if action == "fire_shutdown":
            signs = evidence.fire_indicators(machine, m, ctx.peers, ctx.memory)
            return len(signs) >= 2, f"fire signs: {signs}"
        if action == "emergency_ventilation":
            gas = evidence.gas_hazard(machine, m, ctx.peers)
            fire = evidence.fire_indicators(machine, m, ctx.peers, ctx.memory)
            return bool(gas) and not fire, f"gas signs: {gas}; fire signs: {fire}"
        if action == "deploy_shutters":
            # The controller only trusts the warning feed; hurricane-force wind measured on site is
            # confirmation enough for Jarvis.
            tornado = evidence.tornado_state(m, ctx.peers, machine)
            return tornado["warning"] or tornado["extreme_wind"], f"tornado warning feed or extreme wind: {tornado}"
        if action == "deploy_flood_barrier":
            flood = evidence.flood_state(m, ctx.peers, machine)
            return flood["warning"] or flood["level_cm"] >= 5.0 or flood["intrusion"], f"flood: {flood}"
        if action == "activate_alarm":
            signs = evidence.fire_indicators(machine, m, ctx.peers, ctx.memory)
            gas = evidence.gas_hazard(machine, m, ctx.peers)
            return len(signs) >= 2 or bool(gas), f"fire {signs}, gas {gas}"
        return False, "unknown action"

    # ------------------------------------------------------------ actions
    def apply_action(self, output: NodeOutput, ctx) -> dict | None:
        if output.action == "none":
            return None
        if ctx.memory.action_results.get(output.action) in ("REJECTED", "FAILED"):
            return None  # the machine refused this action before; the brain must find another way
        decision = {"tick": ctx.tick, "node": ctx.node_id, "action": output.action, "brain": output.brain}
        allowed, reason = output.assessment == "DANGER", "requires DANGER"
        if allowed:
            allowed, reason = self._action_justified(output.action, ctx)
        key = (ctx.node_id, output.action)
        if allowed and ctx.time_s - self.last_action_s.get(key, -1e18) < ACTION_COOLDOWN_S:
            return None  # already done within the cooldown; nothing new to decide
        decision.update(allowed=allowed, reason=reason)
        if allowed:
            kind, arguments = ACTION_COMMANDS[output.action]
            # Own machine only: the command target is always this node's device.
            command = self.engine.submit_command(ctx.node_id, kind, dict(arguments), source=jarvis_id(ctx.node_id))
            self.last_action_s[key] = ctx.time_s
            self.pending.append({"command_id": command["command_id"], "node": ctx.node_id, "action": output.action,
                                 "submitted_tick": ctx.tick})
            decision["command_id"] = command["command_id"]
            ctx.memory.mark(action=output.action)
        self.decisions.append(decision)
        return decision

    def verify(self) -> list[dict]:
        """Resolve commands whose Matrix lifecycle finished; returns the newly resolved ones."""
        done = []
        for record in list(self.pending):
            status = self.engine.world.commands[record["command_id"]]["status"]
            if status in ("VERIFIED", "VERIFICATION_FAILED", "REJECTED", "FAILED", "TIMEOUT"):
                record["status"] = status
                self.pending.remove(record)
                done.append(record)
        return done

    # ------------------------------------------------------------ communications
    def _lead(self, kind, ctx) -> str:
        """Who speaks for an incident: the machine with the strongest first-hand evidence."""
        if kind == "fire":
            apu_has_evidence = any(sign in ("smoke_detector", "carbon_monoxide")
                                   for sign in evidence.fire_indicators("animal_protection", None, {
                                       s: m for s, m in ctx.peers.items() if s == "animal-protection"}))
            if ctx.node_id == "animal-protection":
                apu_has_evidence = apu_has_evidence or any(
                    sign in ("smoke_detector", "carbon_monoxide")
                    for sign in evidence.fire_indicators("animal_protection", ctx.monitoring, {}))
            return "animal-protection" if apu_has_evidence else "hvac"
        if kind in ("gas", "flood", "storm", "water"):  # water: it measures the main's pressure
            return "animal-protection"
        return ctx.node_id

    def apply_communication(self, output: NodeOutput, ctx, incident_id: str | None) -> dict | None:
        if output.communication == "none" or incident_id is None:
            return None
        comms = self.engine.comms
        caller = jarvis_id(ctx.node_id)
        decision = {"tick": ctx.tick, "node": ctx.node_id, "communication": output.communication, "brain": output.brain}
        machine = MACHINE_TYPES[ctx.node_id]
        incident = ctx.memory.active()
        if output.communication == "notify_owner":
            priority = PRIORITY[output.assessment]
            ranks = ("info", "warning", "urgent", "emergency")
            if incident and incident["notified_priority"] and ranks.index(priority) <= ranks.index(incident["notified_priority"]):
                return None  # the owner already has this incident at this priority
            told = self.owner_told.get((ctx.node_id, output.diagnosis))
            if told and ctx.time_s - told[0] < OWNER_REPEAT_S and ranks.index(priority) <= ranks.index(told[1]):
                ctx.memory.mark(notified=True, priority=told[1])
                return None  # same problem reported recently; do not nag the owner
            if output.assessment not in ("ESCALATE_TO_USER", "DANGER"):
                decision.update(allowed=False, reason="owner is notified only on escalation or danger")
            else:
                record = comms.notify_owner(sender=caller, incident_id=incident_id, priority=PRIORITY[output.assessment],
                                            summary=f"{ctx.node_id}: {output.diagnosis} - {output.recommendation}")
                ctx.memory.mark(notified=True, priority=priority)
                self.owner_told[(ctx.node_id, output.diagnosis)] = (ctx.time_s, priority)
                decision.update(allowed=True, reason="escalation", notification_id=record["notification_id"],
                                merged=record.get("merged", False))
            self.decisions.append(decision)
            return decision
        service = "fire_brigade" if output.communication == "call_fire_brigade" else "civil_protection"
        if ctx.memory.call_active(service) or (incident and service in incident["calls"]):
            return None  # already called (by this node or, as the peers report, by another)
        if service == "fire_brigade":
            signs = evidence.fire_indicators(machine, ctx.monitoring, ctx.peers, ctx.memory)
            justified, reason = output.assessment == "DANGER" and len(signs) >= 2, f"fire signs: {signs}"
            kind = "fire"
        else:
            flood = evidence.flood_state(ctx.monitoring, ctx.peers, machine)
            tornado = evidence.tornado_state(ctx.monitoring, ctx.peers, machine)
            notes = [n for n in ctx.comms_status["notifications"] if n["incident_id"] == incident_id]
            owner_silent = bool(notes) and all(n["status"] != "acknowledged" for n in notes) and \
                ctx.time_s - min(n["sent_s"] for n in notes) >= OWNER_GRACE_S
            emergency = flood["intrusion"] or flood["level_cm"] >= 30.0 or tornado["extreme_wind"]
            justified = output.assessment == "DANGER" and emergency and owner_silent
            reason = f"flood {flood}, tornado {tornado}, owner silent {owner_silent}"
            kind = "flood" if flood["intrusion"] or flood["level_cm"] >= 30.0 else "storm"
        lead = self._lead(kind, ctx)
        lead_called = any(service_name == service for s, m in ctx.peers.items() if s == lead for service_name in m.calls_made)
        incident = ctx.memory.active()
        lead_silent = incident is not None and ctx.time_s - incident["opened_s"] >= LEAD_GRACE_S and not lead_called
        if justified and lead != ctx.node_id and not lead_silent:
            justified, reason = False, f"{lead} leads this incident"
        decision.update(allowed=justified, reason=reason)
        if not justified:
            last = self._last_refusal.get((ctx.node_id, service))
            self._last_refusal[(ctx.node_id, service)] = reason.split("(")[0][:40]
            if last == self._last_refusal[(ctx.node_id, service)]:
                return None  # same refusal as last time; keep the audit trail readable
        if justified:
            record = comms.call_emergency(service=service, sender=caller, incident_id=incident_id,
                                          nature=output.diagnosis, location=output.recommendation)
            comms.notify_owner(sender=caller, incident_id=incident_id, priority="emergency",
                               summary=f"{ctx.node_id}: {output.diagnosis}; {service.replace('_', ' ')} called. {output.recommendation}")
            ctx.memory.mark(notified=True, call=service, priority="emergency")
            ctx.memory.note_call_status(ctx.time_s, {service: record["status"]})
            decision.update(call_id=record["call_id"], merged=record.get("merged", False))
        self.decisions.append(decision)
        return decision
