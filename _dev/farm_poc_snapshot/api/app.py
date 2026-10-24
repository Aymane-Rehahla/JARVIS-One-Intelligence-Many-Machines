"""Small API wrapper; simulation remains usable without an HTTP server."""
from fastapi import FastAPI,HTTPException,WebSocket,WebSocketDisconnect
from pydantic import BaseModel,Field,ConfigDict,create_model
from typing import Any,Literal
from ..matrix.engine import MatrixEngine
from ..matrix.scenario import OvervoltageScenario

class CommandRequest(BaseModel): target_id:str; command_type:str; arguments:dict[str,Any]=Field(default_factory=dict); source_id:str="api"
class ScenarioRequest(BaseModel): inject_tick:int=Field(default=2,ge=0); voltage_v:float=Field(default=263.0,gt=0,allow_inf_nan=False); peer_modes:dict[str,Any]=Field(default_factory=dict); action:str="none"; action_fails:bool=False; action_unverifiable:bool=False; action_arguments:dict[str,Any]=Field(default_factory=dict); recovery_tick:int|None=Field(default=None,ge=0); recovery_voltage_v:float=Field(default=230.0,gt=0,allow_inf_nan=False)
class HealthResponse(BaseModel): status:Literal["ok"]; tick:int
class ErrorResponse(BaseModel): detail:str
class RunningResponse(BaseModel): running:bool; tick:int
class PausedResponse(BaseModel): paused:bool
class PublicEvent(BaseModel): sequence:int; episode_id:str; tick:int; time_s:float; type:str; source_id:str|None; data:dict[str,Any]
class StrictModel(BaseModel):
    model_config=ConfigDict(extra="forbid")

class IncubatorFields(StrictModel):
    online:bool; health:Literal["normal","degraded","fault"]
    ac_voltage_v:float; frequency_hz:float; ac_current_a:float; real_power_w:float; apparent_power_va:float; power_factor:float; energy_kwh:float
    heater_current_a:float; heater_power_w:float
    temperatures_c:list[float|None]; temperature_mean_c:float|None; temperature_min_c:float|None; temperature_max_c:float|None; heater_outlet_c:float|None
    humidities_pct:list[float|None]; humidity_mean_pct:float|None; humidity_min_pct:float|None; humidity_max_pct:float|None
    reservoir_pct:float; fan_command_pct:float; fan_rpm:list[int]; airflow_m3_h:float|None
    heater_on:bool; humidifier_on:bool; pump_on:bool; door_open:bool; door_open_seconds:float; lamp_on:bool
    egg_type:str; batch:str; incubation_day:int; days_remaining:int; expected_hatch:str; eggs:int; turning:bool
    sensor_health:dict[str,str]; sensor_calibrated:bool; power_status:str; overtemperature_cutoff:bool
    faults:list[str]; alarms:list[str]; last_telemetry_tick:float
class Incubator1Telemetry(IncubatorFields): device_id:Literal["incubator-1"]
class Incubator2Telemetry(IncubatorFields): device_id:Literal["incubator-2"]
class HVACZone(BaseModel):
    model_config=ConfigDict(extra="allow")
    temperature_c:float|None; humidity_pct:float|None
class HVACOutdoor(StrictModel): temperature_c:float|None; humidity_pct:float|None
class HVACTelemetry(StrictModel):
    device_id:Literal["hvac"]; online:bool; zones:dict[str,HVACZone]; outdoor:HVACOutdoor|None
    supply_c:float|None; return_c:float|None; cooling:bool; fan_command_pct:float; fan_speed_pct:float
    airflow_m3_h:float|None; damper_positions_pct:dict[str,float]; power_w:float|None; faults:list[str]; health:Literal["normal","fault"]
class AnimalEnvironment(BaseModel):
    model_config=ConfigDict(extra="allow")
    temperature_c:float|None; humidity_pct:float|None
class AnimalProtectionTelemetry(StrictModel):
    device_id:Literal["animal-protection"]; online:bool; feed_available:bool; water_available:bool
    co2_ppm:float|None; o2_pct:float|None; ammonia_ppm:float|None; environment:AnimalEnvironment
    weather_warning:str|None; flood:bool; windows_pct:float; vents_pct:float; emergency_ready:bool
    alarms:list[str]; faults:list[str]; health:Literal["normal","fault"]

_CAPABILITY_NULLABLE_FIELDS={
    "pressure_tendency_hpa_3h","weather_watch","watch_issue_time_s","watch_valid_until_s","backup_battery_charge_pct",
    "co_ppm_by_zone","co_ppm","smoke_obscuration_pct_m","smoke_detector_health","heater_outlet_c","heater_outlet_temperature_c",
    "heater_outlet_delta_c","fan1_speed_rpm","refrigerant_pressure_kpa",
    "ambient_temperature_c","ambient_relative_humidity_pct","water_flow_rate_ml_min","water_inlet_flow_ml_min","water_leak_detected",
    "air_pressure_differential_pa","damper_actual_pct","door_lamp_current_a","door_switch_health","fan2_speed_rpm",
    "water_system_current_a","egg_breed","turning_speed_rpm","turning_position_deg","measurement_quality",
    "backup_power_status","backup_power_alert","outdoor","supply_air_humidity_pct","return_air_humidity_pct",
    "supply_airflow_m3_h","return_airflow_m3_h","duct_static_pressure_pa","filter_differential_pressure_pa",
    "total_apparent_power_va","power_factor","compressor_feedback_on","compressor_current_a","return_fan_actual_rpm",
    "return_fan_current_a","damper_positions_pct","cooling_valve_actual_pct","refrigerant_pressure_kpa",
    "refrigerant_temperature_c","condensate_leak_detected","internal_controller_temperature_c","filter_status",
    "feed_available","water_available","feed_storage_level_pct","feed_storage_remaining_kg","feed_hopper_levels_pct",
    "feed_dispensing_rate_kg_h","feeder_blocked","feed_sensor_health","feed_supply_status","water_tank_level_pct","water_tank_volume_l",
    "water_flow_rate_l_min","water_pressure_kpa","water_pump_state","water_availability_status","water_leak_detected","water_sensor_health",
    "other_gases_ppm_by_zone","smoke_detected","wind_speed_m_s","wind_direction_deg","rainfall_rate_mm_h",
    "cumulative_rainfall_mm","floodwater_level_cm","flood","water_intrusion_detected","external_temperature_c",
    "barometric_pressure_hpa","severe_weather_warning","tornado_warning","flood_warning","regional_hazard_warning",
    "warning_issue_time_s","warning_valid_until_s","warning_source","window_positions_pct","window_actual_positions_pct",
    "vent_positions_pct","vent_actual_positions_pct","door_gate_positions","flood_barrier_state","emergency_shutter_state",
    "closure_confirmation","actuator_health","backup_battery_charge_pct","backup_power_available","population_count",
    "animal_activity","inactivity_alert","animal_monitoring_sensor_health","feed_availability_duration_h",
    "water_availability_duration_h","data_freshness_s","turning_fault",
}

def _field_annotation(name,value):
    if name == "device_id":
        return str
    if name == "flood":
        return bool | None
    if name in _CAPABILITY_NULLABLE_FIELDS and value is not None:
        return _field_annotation(f"schema_optional_{name}",value) | None
    if name == "zones":
        return dict[str, HVACZone]
    if name == "environment":
        return AnimalEnvironment
    if value is None:
        if name.endswith(("_on", "_active", "_detected", "_available", "_warning", "_alert")):
            return bool | None
        if name.endswith(("_s", "_v", "_a", "_w", "_pct", "_c", "_hz", "_rpm", "_kg", "_l", "_pa", "_ppm", "_hpa", "_deg", "_min", "_m3_h", "_kwh")):
            return float | None
        return str | float | int | bool | None
    if isinstance(value,bool): return bool
    if isinstance(value,int): return int
    if isinstance(value,float): return float
    if isinstance(value,str): return str
    if isinstance(value,list):
        if value: return list[_field_annotation(name, value[0])]
        if name in {"faults","alarms","sensor_faults","animal_welfare_alerts","required_protective_actions","safety_conflicts","feed_faults","water_faults","building_faults"}: return list[str]
        if name in {"active_alarms","active_emergency_conditions","requested_actions","emergency_request_outcomes"}: return list[dict[str,Any]]
        return list[Any]
    if isinstance(value,dict):
        vals=list(value.values())
        if not vals: return dict[str,Any]
        present=[item for item in vals if item is not None]
        if not present:return dict[str,Any]
        kinds={type(item) for item in present}
        if len(kinds)>1:return dict[str,Any]
        first=present[0]
        return dict[str,Any] if isinstance(first,dict) else dict[str,_field_annotation(name,first)]
    return Any

def _telemetry_model(name, sample, device_id):
    fields={key:(_field_annotation(key,value), ... if value is not None else None) for key,value in sample.items()}
    fields["device_id"]=(Literal[device_id],device_id)
    return create_model(name,__config__=ConfigDict(extra="forbid"),**fields)

# Build response models from the four authoritative runtime payloads so API
# serialization and OpenAPI cannot silently diverge when device telemetry grows.
_schema_engine=MatrixEngine()
Incubator1Telemetry=_telemetry_model("Incubator1Telemetry",_schema_engine.device("incubator-1"),"incubator-1")
Incubator2Telemetry=_telemetry_model("Incubator2Telemetry",_schema_engine.device("incubator-2"),"incubator-2")
HVACTelemetry=_telemetry_model("HVACTelemetry",_schema_engine.device("hvac"),"hvac")
AnimalProtectionTelemetry=_telemetry_model("AnimalProtectionTelemetry",_schema_engine.device("animal-protection"),"animal-protection")
DeviceTelemetry=Incubator1Telemetry|Incubator2Telemetry|HVACTelemetry|AnimalProtectionTelemetry
class PublicState(BaseModel): episode_id:str; tick:int; devices:dict[str,DeviceTelemetry]; events:list[PublicEvent]; time_s:float=0.0; time_of_day:str|None=None
class ScenarioResponse(BaseModel): started:bool; name:str; inject_tick:int
class CommandResponse(BaseModel): command_id:str; episode_id:str; source_id:str; target_id:str; command_type:str; arguments:dict[str,Any]; timestamp:float; status:Literal["REQUESTED","ACKNOWLEDGED","EXECUTED","VERIFIED","REJECTED","FAILED","TIMEOUT","VERIFICATION_FAILED"]; result:dict[str,Any]|None
class PeerEvidenceResponse(BaseModel): fresh_report_count:int; stale_report_count:int; elevated_voltage_peers:list[str]; normal_voltage_peers:list[str]; missing_peers:list[str]; unavailable_voltage_peers:list[str]
class EvaluationResponse(BaseModel): episode_id:str; tick:int; injected:bool; local_protection:bool; local_controller_credit:bool; ai_supervision_credit:bool; published_alarm:bool; peer_evidence:PeerEvidenceResponse; sufficient_peer_evidence:bool; wider_supply_evidence:bool; conflicting_peer_evidence:bool; root_cause_proven:bool; command_states:dict[str,str]; score:float
def create_app(engine=None):
    matrix=engine or MatrixEngine(); app=FastAPI(title="JARVIS Matrix API",version="0.1.0"); app.state.engine=matrix; app.state.sockets=set()
    @app.get("/health",response_model=HealthResponse)
    def health():return {"status":"ok","tick":matrix.clock.tick}
    @app.get("/state",response_model=PublicState)
    def state():return matrix.snapshot()
    @app.get("/devices",response_model=dict[str,DeviceTelemetry])
    def devices():return {k:v.public_telemetry() for k,v in matrix.world.devices.items()}
    @app.get("/devices/{device_id}",response_model=DeviceTelemetry,responses={404:{"model":ErrorResponse,"description":"Unknown device ID"}})
    def device(device_id):
        try:return matrix.device(device_id)
        except KeyError:raise HTTPException(404,"unknown device")
    @app.get("/events",response_model=list[PublicEvent])
    def events(after:int=0,limit:int=500):return matrix.world.public_events(after, max(0,min(limit,5000)))
    @app.post("/simulation/start",response_model=RunningResponse)
    def start():matrix.paused=False;return {"running":True,"tick":matrix.clock.tick}
    @app.post("/simulation/pause",response_model=PausedResponse)
    def pause():matrix.paused=True;return {"paused":True}
    @app.post("/simulation/resume",response_model=PausedResponse)
    def resume():matrix.paused=False;return {"paused":False}
    @app.post("/simulation/reset",response_model=PublicState)
    async def reset():
        matrix.reset();snapshot=matrix.snapshot();await broadcast({"type":"reset","state":snapshot});return snapshot
    @app.post("/simulation/step",response_model=PublicState)
    async def step():
        snapshot=matrix.step(); await broadcast({"type":"state","state":snapshot}); return snapshot
    @app.post("/scenario/overvoltage",response_model=ScenarioResponse)
    def scenario(req:ScenarioRequest):
        try:matrix.start_scenario(OvervoltageScenario(req.inject_tick,req.voltage_v,req.peer_modes,req.action,req.action_fails,req.action_unverifiable,req.action_arguments,req.recovery_tick,req.recovery_voltage_v))
        except ValueError as exc:raise HTTPException(422,str(exc))
        return {"started":True,"name":"overvoltage","inject_tick":req.inject_tick}
    @app.post("/commands",response_model=CommandResponse,responses={404:{"model":ErrorResponse,"description":"Unknown target device"}})
    def command(req:CommandRequest):
        try:return matrix.submit_command(req.target_id,req.command_type,req.arguments,req.source_id)
        except KeyError:raise HTTPException(404,"unknown target")
    @app.get("/evaluation",response_model=EvaluationResponse)
    def evaluation():return matrix.results[-1] if matrix.results else matrix.evaluator.evaluate(matrix)
    async def broadcast(data):
        stale=[]
        for ws in list(app.state.sockets):
            try:await ws.send_json(data)
            except Exception:stale.append(ws)
        for ws in stale:app.state.sockets.discard(ws)
    @app.websocket("/ws")
    async def websocket(ws:WebSocket):
        await ws.accept();app.state.sockets.add(ws);await ws.send_json({"type":"snapshot","state":matrix.snapshot()})
        try:
            while True:
                message=await ws.receive_json()
                if message.get("type")=="step":await broadcast({"type":"state","state":matrix.step()})
                elif message.get("type")=="pause":matrix.paused=True;await ws.send_json({"type":"paused"})
                elif message.get("type")=="resume":matrix.paused=False;await ws.send_json({"type":"resumed"})
        except WebSocketDisconnect:app.state.sockets.discard(ws)
    return app
app=create_app()
