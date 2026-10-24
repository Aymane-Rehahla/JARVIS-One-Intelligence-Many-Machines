"""Rule-based node brain.

Three jobs: the stand-in for a machine that is not running a real AI, the
baseline the trained CTM must beat, and the source of peer messages during the
first training stage. It is never the source of training labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import evidence
from .contracts import ASSESSMENTS, MACHINE_TYPES, NODE_IDS, NodeOutput
from .memory import NodeMemory

RANK = {name: n for n, name in enumerate(ASSESSMENTS)}
SILENT_AFTER_S = 30.0
PEER_SILENT_AFTER_S = 60.0     # heartbeats arrive every 10 s
PEER_ESCALATE_AFTER_S = 600.0
UNRECOVERED_AFTER_S = 1800.0
OWNER_GRACE_S = 300.0


@dataclass
class NodeContext:
    node_id: str
    tick: int
    time_s: float
    step_seconds: float
    monitoring: dict | None
    observation_age_s: float
    peers: dict[str, Any]
    memory: NodeMemory
    comms_status: dict

    @property
    def machine(self) -> str:
        return MACHINE_TYPES[self.node_id]


@dataclass
class Finding:
    assessment: str
    diagnosis: str
    kind: str | None
    recommendation: str
    evidence: tuple[str, ...] = ()
    action: str = "none"
    confidence: float = 1.0


def _codes(monitoring):
    return {alarm["code"] for alarm in (monitoring or {}).get("alarms", []) if isinstance(alarm, dict)}


def _peer_danger(ctx, kinds):
    return [s for s, m in ctx.peers.items() if m.assessment in ("ESCALATE_TO_USER", "DANGER") and m.incident_kind in kinds]


# ------------------------------------------------------------------ incubator
def _incubator(ctx: NodeContext) -> list[Finding]:
    m, codes, out = ctx.monitoring, _codes(ctx.monitoring), []
    if evidence.incubator_overheating_despite_cutoffs(m):
        out.append(Finding("DANGER", "OVERHEATING", "equipment",
                           "Heater keeps heating past every cutoff; heater circuit stopped. Inspect relay and high-limit.",
                           ("room_temperature_high", "heater_current_present"), "emergency_stop_heater"))
    if "BACKUP_BATTERY_LOW" in codes:
        out.append(Finding("ESCALATE_TO_USER", "POWER_OUTAGE", "power", "Battery nearly empty during the outage; restore mains power."))
    elif "POWER_OUTAGE" in codes:
        out.append(Finding("HANDLED_BY_MACHINE", "POWER_OUTAGE", "power", "Running on backup battery; heater off until mains returns."))
    if codes & {"OVERVOLTAGE", "OVERVOLTAGE_PROTECTION"}:
        out.append(Finding("HANDLED_BY_MACHINE", "SUPPLY_OVERVOLTAGE", "power", "Overvoltage relay cut the heater; check the supply."))
    if codes & {"OVERTEMPERATURE", "OVERTEMPERATURE_PROTECTION"}:
        out.append(Finding("HANDLED_BY_MACHINE", "OVERHEATING", "equipment", "High-limit thermostat cut the heater."))
    for codeset, diagnosis, advice in ((("HEATER_RELAY_STUCK", "HEATER_OPEN_CIRCUIT"), "HEATER_FAULT", "Heater circuit fault; needs repair."),
                                       (("FAN1_FAILURE", "FAN2_FAILURE"), "FAN_FAULT", "Fan failed; replace it."),
                                       (("PUMP_FAULT", "HUMIDIFIER_FAULT"), "HUMIDIFICATION_FAULT", "Humidifier not working; check pump and element."),
                                       (("WATER_LEAK",), "WATER_LEAK", "Water leak in the incubator."),
                                       (("TURNING_FAULT",), "TURNING_FAULT", "Egg turning stopped; turn by hand and repair the motor."),
                                       (("DOOR_SWITCH_FAULT",), "DOOR_PROBLEM", "Door switch failed; door state unknown."),
                                       (("INTERNAL_POWER_FAULT",), "INTERNAL_POWER_FAULT", "Internal power supply fault.")):
        if codes & set(codeset):
            out.append(Finding("ESCALATE_TO_USER", diagnosis, "equipment", advice, tuple(sorted(codes & set(codeset)))))
    if m:
        temps = [t for t in m["room_temperatures_c"] if t is not None]
        if temps and len(temps) < len(m["room_temperatures_c"]) or (len(temps) >= 2 and max(temps) - min(temps) > 1.0):
            out.append(Finding("WATCHING", "SENSOR_FAULT", "equipment", "Room sensors disagree; checking.", ("sensor_spread",), confidence=0.6))
        # Expectation: a heater working this hard should hold temperature.
        working = ctx.memory.sustained("heater_output_pct", 90.0, 600.0, ctx.time_s) and temps and sum(temps) / len(temps) < 37.2
        if working:
            out.append(Finding("WATCHING", "DOOR_PROBLEM", "equipment",
                               "Heater at full power but the cabinet stays cool: door open or heater weak.", ("heat_balance",), confidence=0.6))
        if temps and min(temps) > 38.5 and m["heating"]["power_w"] < 1.0 and not m["safety"]["overtemperature_trip"]:
            out.append(Finding("WATCHING", "OVERHEATING", "environment", "Warm with the heater off: external heat?", ("heat_without_heater",), confidence=0.6))
    if _peer_danger(ctx, ("fire", "gas", "flood", "storm")):
        out.append(Finding("WATCHING", "EXTERNAL_HAZARD", "environment", "Another system reports a hazard; watching conditions here.",
                           ("peer_hazard",), confidence=0.7))
    out += _incubator_water(ctx, codes)
    return out


def _incubator_water(ctx: NodeContext, codes) -> list[Finding]:
    """The reservoir refills from the farm main through a hose: is it our hose, or the whole farm?"""
    farm_cut = evidence.farm_water_cut(ctx.machine, ctx.monitoring, ctx.peers, ctx.memory)
    dry_s = ctx.memory.dry_pumping_s
    if farm_cut:
        if "LOW_WATER" in codes:
            return [Finding("ESCALATE_TO_USER", "WATER_SUPPLY_LOSS", "water", "Farm water is off and the reservoir is "
                            "nearly empty; humidity will fall.", ("water_supply_lost", "low_water"))]
        return [Finding("WATCHING", "WATER_SUPPLY_LOSS", "water", "Farm water main is down; the reservoir is not refilling.",
                        ("water_supply_lost",), confidence=0.8)]
    if "LOW_WATER" in codes or dry_s >= 600.0:
        return [Finding("ESCALATE_TO_USER", "WATER_INLET_FAULT", "equipment", "No water reaches this incubator; check its "
                        "hose and fill valve.", ("inlet_dry",) + (("low_water",) if "LOW_WATER" in codes else ()))]
    if dry_s >= 120.0:
        return [Finding("WATCHING", "WATER_INLET_FAULT", "equipment", "Pump running but no water coming in.", ("inlet_dry",),
                        confidence=0.6)]
    return []


# ------------------------------------------------------------------ HVAC
def _hvac(ctx: NodeContext) -> list[Finding]:
    m, codes, out = ctx.monitoring, _codes(ctx.monitoring), []
    fire = evidence.fire_indicators(ctx.machine, m, ctx.peers, ctx.memory)
    if len(fire) >= 2:
        out.append(Finding("DANGER", "FIRE_SUSPECTED", "fire", "Fire likely: air handler shut down to stop spreading smoke.",
                           tuple(fire), "fire_shutdown" if not m or m["mode"] != "fire_shutdown" else "none"))
    elif fire:
        out.append(Finding("WATCHING", "FIRE_SUSPECTED", "fire", "One sign of fire; asking the other systems.", tuple(fire), confidence=0.6))
    gas = evidence.gas_hazard(ctx.machine, m, ctx.peers)
    if gas and not fire:
        action = "emergency_ventilation" if not m or m["mode"] not in ("emergency_ventilation", "fire_shutdown") else "none"
        out.append(Finding("DANGER", "AIR_QUALITY_HAZARD", "gas", "Dangerous barn air: switched to emergency ventilation.", tuple(gas), action))
    for code, assessment, diagnosis, advice in (
            ("SUPPLY_FAN_FAILURE", "ESCALATE_TO_USER", "SUPPLY_FAN_FAULT", "Supply fan failed; no ventilation or cooling."),
            ("RETURN_FAN_FAILURE", "ESCALATE_TO_USER", "RETURN_FAN_FAULT", "Return fan failed."),
            ("COMPRESSOR_FAILURE", "ESCALATE_TO_USER", "COMPRESSOR_FAULT", "Compressor will not run; call a technician."),
            ("CONDENSATE_OVERFLOW", "HANDLED_BY_MACHINE", "CONDENSATE_PROBLEM", "Float switch stopped cooling: clear the condensate drain."),
            ("CONDENSATE_LEAK", "ESCALATE_TO_USER", "CONDENSATE_PROBLEM", "Condensate is leaking from the unit."),
            ("LOW_REFRIGERANT_PRESSURE", "HANDLED_BY_MACHINE", "REFRIGERANT_LEAK", "Low-pressure switch locked the compressor: refrigerant leak likely."),
            ("FILTER_MAINTENANCE", "ESCALATE_TO_USER", "FILTER_CLOGGED", "Replace the air filter.")):
        if code in codes:
            out.append(Finding(assessment, diagnosis, "equipment", advice, (code,)))
    if m:
        cooling = m["cooling"]
        supply, ret = m["air_handling"]["supply_temperature_c"], m["air_handling"]["return_temperature_c"]
        if cooling["state"] == "on" and (cooling["output_pct"] or 0) >= 70 and supply is not None and ret is not None and ret - supply < 4.0:
            out.append(Finding("WATCHING", "REFRIGERANT_LEAK", "equipment",
                               "Cooling hard but the coil barely cools the air: refrigerant or coil problem?", ("weak_coil_delta",), confidence=0.6))
    return out


# ------------------------------------------------------------------ Animal Protection
def _apu(ctx: NodeContext) -> list[Finding]:
    m, codes, out = ctx.monitoring, _codes(ctx.monitoring), []
    fire = evidence.fire_indicators(ctx.machine, m, ctx.peers, ctx.memory)
    alarm_active = bool(m and m["emergency_readiness"]["alarm_system"] == "active")
    if len(fire) >= 2:
        out.append(Finding("DANGER", "FIRE_SUSPECTED", "fire", "Fire confirmed by two independent signs; alarm on, fire brigade called.",
                           tuple(fire), "activate_alarm" if not alarm_active else "none"))
    elif fire:
        out.append(Finding("ESCALATE_TO_USER", "FIRE_SUSPECTED", "fire", "Smoke or CO detected; please verify now.", tuple(fire), confidence=0.7))
    gas = evidence.gas_hazard(ctx.machine, m, ctx.peers)
    if "oxygen_low" in gas or "carbon_monoxide_dangerous" in gas:
        out.append(Finding("DANGER", "GAS_HAZARD", "gas", "Air in the barn is dangerous; ventilate and keep people out.", tuple(gas)))
    elif codes & {"CO2_HIGH", "AMMONIA_HIGH", "CO_HIGH"}:
        out.append(Finding("HANDLED_BY_MACHINE", "GAS_HAZARD", "gas", "Barn air quality poor; vents opened to purge.", tuple(sorted(codes & {"CO2_HIGH", "AMMONIA_HIGH", "CO_HIGH"}))))
    flood = evidence.flood_state(m, ctx.peers, ctx.machine)
    if flood["intrusion"]:
        out.append(Finding("DANGER", "FLOOD", "flood", "Water is entering the barn.", ("water_intrusion",)))
    elif flood["level_cm"] >= 5.0 or flood["warning"]:
        barrier = m["building_protection"]["flood_barrier"] if m else None
        if barrier in ("deployed", "deploying"):
            out.append(Finding("HANDLED_BY_MACHINE", "FLOOD", "flood", "Floodwater rising; barrier deployed, low vents closed."))
        elif ctx.memory.sustained("floodwater_level_cm", 5.0, 180.0, ctx.time_s) or                 (flood["warning"] and ctx.memory.sustained("flood_warning", 1.0, 180.0, ctx.time_s)):
            out.append(Finding("DANGER", "FLOOD", "flood", "Flood barrier did not deploy; deploying it.", ("barrier_missing",), "deploy_flood_barrier"))
        else:
            out.append(Finding("WATCHING", "FLOOD", "flood", "Floodwater rising; waiting for the barrier.", confidence=0.7))
    tornado = evidence.tornado_state(m, ctx.peers, ctx.machine)
    if tornado["extreme_wind"]:
        shutters = m["building_protection"]["storm_shutters"] if m else None
        out.append(Finding("DANGER", "SEVERE_WEATHER", "storm", "Tornado winds on site." if tornado["warning"]
                           else "Hurricane-force wind on site.", ("extreme_wind",),
                           "deploy_shutters" if shutters in ("standby", "retracting") else "none"))
    elif tornado["warning"]:
        shutters = m["building_protection"]["storm_shutters"] if m else None
        if shutters in ("deployed", "deploying"):
            out.append(Finding("ESCALATE_TO_USER", "SEVERE_WEATHER", "storm", "Tornado warning: shutters deployed, barn closed. Take shelter."))
        elif ctx.memory.active() and ctx.time_s - ctx.memory.active()["opened_s"] > 120:
            out.append(Finding("DANGER", "SEVERE_WEATHER", "storm", "Shutters did not deploy under a tornado warning; deploying.", ("shutters_missing",), "deploy_shutters"))
        else:
            out.append(Finding("ESCALATE_TO_USER", "SEVERE_WEATHER", "storm", "Tornado warning issued."))
    elif m and m["disaster_alerts"]["severe_weather_warning"]:
        out.append(Finding("ESCALATE_TO_USER", "SEVERE_WEATHER", "storm", "Severe weather warning: windows closed."))
    elif m and m["disaster_alerts"]["risk_state"] == "watch":
        out.append(Finding("WATCHING", "SEVERE_WEATHER", "storm", "Weather watch or worsening local weather.", confidence=0.7))
    if "BACKUP_BATTERY_LOW" in codes:
        out.append(Finding("ESCALATE_TO_USER", "POWER_OUTAGE", "power", "Backup battery low during the outage."))
    elif "MAINS_POWER_FAILURE" in codes:
        out.append(Finding("HANDLED_BY_MACHINE", "POWER_OUTAGE", "power", "Mains failed; running on battery. Drinker pump is off."))
    if "WATER_SUPPLY_LOST" in codes:
        out.append(Finding("ESCALATE_TO_USER", "WATER_SUPPLY_LOSS", "water", "Farm water main has no pressure; drinkers run "
                           "from the tank and the incubators cannot refill.", ("WATER_SUPPLY_LOST",)))
    for codeset, diagnosis, advice in ((("FEEDER_BLOCKED", "FEED_EMPTY"), "FEED_PROBLEM", "Feed is not reaching the birds."),
                                       (("WATER_PUMP_FAULT", "WATER_LEAK"), "WATER_PROBLEM", "Drinker water problem."),
                                       (("TANK_FILL_FAULT",), "WATER_PROBLEM", "Tank fill valve passes no water; the main is fine."),
                                       (("SMOKE_DETECTOR_FAILED",), "SENSOR_FAULT", "Smoke detector failed.")):
        if codes & set(codeset):
            out.append(Finding("ESCALATE_TO_USER", diagnosis, "equipment", advice, tuple(sorted(codes & set(codeset)))))
    if "HEAT_STRESS" in codes:
        out.append(Finding("HANDLED_BY_MACHINE", "HEAT_STRESS", "environment", "Barn too hot; cooling requested, windows managed."))
    if m:
        # Expectation: rain with open windows should make the barn more humid.
        rain = m["weather"]["rainfall_mm_h"] or 0.0
        windows = list((m["building_protection"]["windows_pct"] or {}).values())
        change = ctx.memory.change_over("barn_humidity_pct", 1800.0, ctx.time_s)
        if rain >= 2.0 and windows and sum(windows) / len(windows) > 20.0 and change is not None and change < 0.5:
            out.append(Finding("WATCHING", "SENSOR_FAULT", "equipment", "Raining with windows open but barn humidity is not rising.",
                               ("rain_without_humidity",), confidence=0.5))
    return out


_FINDERS = {"incubator": _incubator, "hvac": _hvac, "animal_protection": _apu}


class RuleBrain:
    name = "rule"

    def decide(self, ctx: NodeContext) -> NodeOutput:
        findings = _FINDERS[ctx.machine](ctx)
        silent = [peer for peer in NODE_IDS if peer != ctx.node_id and ctx.time_s > PEER_SILENT_AFTER_S and
                  (peer not in ctx.peers or ctx.time_s - ctx.peers[peer].sent_tick * ctx.step_seconds > PEER_SILENT_AFTER_S)]
        if silent:
            oldest = max((ctx.time_s - ctx.peers[p].sent_tick * ctx.step_seconds) if p in ctx.peers else ctx.time_s
                         for p in silent)
            findings.append(Finding("ESCALATE_TO_USER" if oldest > PEER_ESCALATE_AFTER_S else "WATCHING",
                                    "COMMUNICATION_LOSS", "network",
                                    f"No messages from {', '.join(silent)}; checking the network.",
                                    tuple(f"silent:{p}" for p in silent), confidence=0.8))
        if ctx.memory.fire_resolved_s is not None:
            # Responders are on scene: the fire is theirs now; we keep watching.
            findings = [f if f.kind != "fire" else Finding("HANDLED_BY_MACHINE" if f.assessment != "WATCHING" else "WATCHING",
                                                         f.diagnosis, "fire", "Fire brigade on scene.", f.evidence)
                        for f in findings]
        if ctx.monitoring is None or ctx.observation_age_s > SILENT_AFTER_S:
            findings.append(Finding("ESCALATE_TO_USER", "POWER_OUTAGE" if ctx.machine != "incubator" or not ctx.monitoring
                                    or ctx.monitoring["electricity"]["power_source"] != "mains" else "INTERNAL_POWER_FAULT",
                                    "power", "My machine has stopped reporting.", ("device_silent",)))
        incident = ctx.memory.active()
        if ctx.memory.fire_resolved_s is not None and ctx.memory.lost_while_hot:
            findings.append(Finding("ESCALATE_TO_USER", "SENSOR_FAULT" if ctx.machine != "incubator" else "EXTERNAL_HAZARD",
                                    "equipment", "Sensors destroyed by the fire must be replaced.", ("fire_damaged_sensor",)))
        # A confirmed hazard stays confirmed until responders deal with it; our own protective
        # action (for example stopping the fans) can hide the evidence that triggered it.
        if (incident and incident["max_assessment"] == "DANGER" and incident["kind"] in ("fire", "gas", "flood", "storm")
                and not (incident["kind"] == "fire" and ctx.memory.fire_resolved_s is not None)
                and not any(f.assessment == "DANGER" and f.kind == incident["kind"] for f in findings)):
            findings.append(Finding("DANGER", incident["diagnosis"], incident["kind"],
                                    "Hazard confirmed earlier; holding protective state until responders report.",
                                    ("confirmed_hazard_latched",)))
        if not findings:
            return NodeOutput(ctx.node_id, ctx.tick, "NORMAL", "NONE", 1.0, None, "", brain=self.name)
        top = max(findings, key=lambda f: (RANK[f.assessment], f.confidence))
        assessment = top.assessment
        # The owner's escalation rules: repeats, no recovery, spreading.
        if assessment in ("HANDLED_BY_MACHINE", "WATCHING"):
            repeats = ctx.memory.recurrence_24h(top.kind, top.diagnosis, ctx.time_s) if top.kind else 0
            unrecovered = incident is not None and incident["kind"] == top.kind and ctx.time_s - incident["opened_s"] > UNRECOVERED_AFTER_S
            spread = [s for s, msg in ctx.peers.items() if msg.incident_kind == top.kind and msg.assessment != "NORMAL"]
            if (assessment == "HANDLED_BY_MACHINE" and (repeats >= 3 or unrecovered)) or (spread and top.kind in ("fire", "gas", "flood", "storm")):
                assessment = "ESCALATE_TO_USER"
        communication = self._communication(ctx, assessment, top)
        failed = ctx.memory.action_results.get(top.action) in ("REJECTED", "FAILED")
        recommendation = top.recommendation + (f" ({top.action} was refused by the machine; manual action needed.)" if failed else "")
        return NodeOutput(ctx.node_id, ctx.tick, assessment, top.diagnosis, top.confidence, top.kind, recommendation,
                          top.action if assessment == "DANGER" and not failed else "none", communication,
                          tuple(top.evidence), {}, self.name)

    @staticmethod
    def _communication(ctx, assessment, top) -> str:
        if assessment == "DANGER" and top.kind == "fire" and top.diagnosis == "FIRE_SUSPECTED":
            return "call_fire_brigade"
        if assessment == "DANGER" and top.kind in ("flood", "storm"):
            incident = ctx.memory.active()
            notified = [n for n in ctx.comms_status["notifications"] if incident and n["incident_id"] == incident["incident_id"]]
            unanswered = notified and all(n["status"] != "acknowledged" for n in notified) and \
                ctx.time_s - min(n["sent_s"] for n in notified) >= OWNER_GRACE_S
            flood = evidence.flood_state(ctx.monitoring, ctx.peers, ctx.machine)
            emergency = flood["intrusion"] or flood["level_cm"] >= 30.0 or evidence.tornado_state(ctx.monitoring, ctx.peers, ctx.machine)["extreme_wind"]
            return "call_civil_protection" if unanswered and emergency else "notify_owner"
        if assessment in ("ESCALATE_TO_USER", "DANGER"):
            return "notify_owner"
        return "none"
