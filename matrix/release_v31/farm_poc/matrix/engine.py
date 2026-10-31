"""Deterministic authoritative Matrix engine independent of any frontend."""
import gzip,json,time,uuid
from ..fastcopy import json_copy
from pathlib import Path
from .simulation_clock import SimulationClock
from .world_state import WorldState
from .scenario import OvervoltageScenario,SensorFaultScenario,ThermalDisturbanceScenario
from .evaluator import Evaluator
from ..devices import Incubator,HVAC,AnimalProtectionUnit
from ..controllers import IncubatorController,HVACController,AnimalProtectionController
from ..transport.adapter import Message
from ..transport.simulated_io import SimulatedIO
from ..transport.rj45_adapter import SimulatedRJ45Adapter
from ..device_computer import DeviceComputer,IncubatorDeviceComputer
from ..peers.peer_registry import PeerRegistry
from .physical_world import PhysicalWorld
from .weather import WeatherModel
from .equipment_faults import EquipmentFaultScenario,PowerOutageScenario,SensorChannelFaultScenario,WaterCutScenario,WeatherEventScenario
from .communications import CommunicationsService

class MatrixEngine:
    def __init__(self,seed=1,config=None,log_path=None):
        self.seed=seed; self.config=config or {}; self.log_path=Path(log_path) if log_path else None; self.listeners=[]; self._event_stream=None; self._episode_index=-1; self.reset()
    def reset(self):
        self._episode_index+=1
        self.clock=SimulationClock(self.config.get("step_seconds",1.0)); self.world=WorldState(str(uuid.uuid5(uuid.NAMESPACE_OID,f"testbed-{self.seed}-{self._episode_index}"))); self.hidden_truth=None; self.active_sensor_fault=None; self.peer_reports={}; self.peer_missing=set(); self.pending_peer_queries={}; self.peer_timeout_ticks=int(self.config.get("peer_response_timeout_ticks",4)); self.peer_freshness_ticks=int(self.config.get("peer_freshness_ticks",4)); self.transport=SimulatedIO(self.config.get("message_history_limit",128)); self.pending={}; self.forced_failures=set(); self.forced_unverifiable=set(); self.sequence=0; self.paused=False; self.scenario=None; self.scenario_applied=False; self.evaluator=Evaluator(); self.results=[]; self.profile_metrics=[]; self.fault_log=[]
        inc1_config={**self.config.get("incubator1",{}),"external_physics":True}
        inc2_config={**self.config.get("incubator2",{}),"external_physics":True}
        hvac_config={**self.config.get("hvac",{}),"external_physics":True}
        self.world.devices={"incubator-1":Incubator("incubator-1",inc1_config),"incubator-2":Incubator("incubator-2",inc2_config),"hvac":HVAC("hvac",hvac_config),"animal-protection":AnimalProtectionUnit("animal-protection",self.config.get("animal_protection",{}))}
        self.controllers={"incubator-1":IncubatorController(self.world.devices["incubator-1"]),"incubator-2":IncubatorController(self.world.devices["incubator-2"]),"hvac":HVACController(self.world.devices["hvac"]),"animal-protection":AnimalProtectionController(self.world.devices["animal-protection"])}
        self.jarvis_link=SimulatedRJ45Adapter(self.config.get("message_history_limit",128)); self.device_computer=IncubatorDeviceComputer(self,self.jarvis_link)
        self.peer_registry=PeerRegistry(self,self.config.get("peer_modes",{}))
        self.device_computers={
            device_id:DeviceComputer(self,device_id,self.transport)
            for device_id in ("incubator-2","hvac","animal-protection")
        }
        self.physical_world=PhysicalWorld(self.world.devices,self.config,seed=f"{self.seed}-{self._episode_index}")
        self.world.physical_state=self.physical_world.state
        weather=self.config.get("weather")
        self.weather=WeatherModel(f"{self.seed}-{self._episode_index}",weather) if weather and weather.get("enabled",True) else None
        if self.weather: self.weather_now=self.weather.apply(self)
        self.comms=CommunicationsService(self,f"{self.seed}-{self._episode_index}",self.config.get("communications"))
        self.emit("reset",None,episode_id=self.world.episode_id)
    def emit(self,event_type,source_id,**data):
        event={"sequence":self.sequence,"episode_id":self.world.episode_id,"tick":self.clock.tick,"time_s":self.clock.time,"type":event_type,"source_id":source_id,"data":json_copy(data)}; self.sequence+=1; self.world.events.append(event)
        for listener in list(self.listeners): listener(json_copy(event))
        if event_type=="command_state" and getattr(self,"device_computer",None):
            self.device_computer.publish_command_event(data.get("command_id"),data.get("status"))
        if event_type=="command_state":
            command=self.world.commands.get(data.get("command_id"))
            if command and command.get("source_id")=="animal-protection":
                apu=self.world.devices["animal-protection"].internal
                record=next((item for item in apu["requested_actions"] if item.get("command_id")==command["command_id"]),None)
                if record is None:
                    record={"command_id":command["command_id"],"target_id":command["target_id"],"action":command["command_type"],"arguments":dict(command["arguments"]),"timestamp":command["timestamp"]}
                    apu["requested_actions"].append(record)
                record["status"]=data.get("status",command["status"])
                record["result"]=command.get("result")
                if command["target_id"]=="hvac":
                    hvac=self.world.devices["hvac"].internal
                    hvac_record=next((item for item in hvac["emergency_requests"] if item.get("command_id")==command["command_id"]),None)
                    if hvac_record is None:
                        hvac_record={"command_id":command["command_id"],"target_id":command["target_id"],"action":command["command_type"],"timestamp":command["timestamp"]}
                        hvac["emergency_requests"].append(hvac_record)
                    hvac_record["status"]=data.get("status",command["status"])
                    hvac_record["result"]=command.get("result")
                    self.world.devices["hvac"]._publish()
                self.world.devices["animal-protection"]._publish()
        if self.log_path:
            self.log_path.parent.mkdir(parents=True,exist_ok=True)
            row=json.dumps(event,sort_keys=True,separators=(",",":"))+"\n"
            if self.config.get("buffered_event_log"):
                if self._event_stream is None:self._event_stream=self._open_event_log()
                self._event_stream.write(row)
            else:
                with self._open_event_log() as f:f.write(row)
        return json_copy(event)
    def _open_event_log(self):
        if self.log_path.suffix == ".gz":
            return gzip.open(self.log_path,"at",encoding="utf-8",newline="\n",compresslevel=1)
        return self.log_path.open("a",encoding="utf-8",newline="\n")
    def subscribe(self,callback): self.listeners.append(callback)
    def start_scenario(self,scenario=None):
        scenario=scenario or OvervoltageScenario()
        if scenario.inject_tick<self.clock.tick:raise ValueError("scenario injection tick is in the past; reset or choose a future tick")
        if scenario.recovery_tick is not None and scenario.recovery_tick <= scenario.inject_tick:
            raise ValueError("recovery tick must be after injection tick")
        if scenario.recovery_voltage_v <= 0:
            raise ValueError("recovery voltage must be positive")
        self.scenario=scenario; self.peer_registry=PeerRegistry(self,self.scenario.peer_modes); self.scenario_applied=False
        self.clock.schedule(scenario.inject_tick,lambda s=scenario:s.apply(self),"overvoltage_injection"); self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
        if scenario.recovery_tick is not None:
            self.clock.schedule(scenario.recovery_tick,lambda s=scenario:s.recover(self),"overvoltage_recovery")
        if self.scenario.action!="none": self.clock.schedule(self.scenario.inject_tick+1,self._scenario_action,"scenario_action")
    def start_sensor_fault(self,scenario=None):
        scenario=scenario or SensorFaultScenario()
        if scenario.inject_tick<self.clock.tick: raise ValueError("sensor fault injection tick is in the past")
        if scenario.device_id not in self.world.devices: raise ValueError("unknown sensor-fault device")
        self.scenario=scenario; self.scenario_applied=False
        self.clock.schedule(scenario.inject_tick,lambda s=scenario:s.apply(self),"sensor_fault_injection")
        self.clock.schedule(scenario.end_tick,lambda s=scenario:s.recover(self),"sensor_fault_recovery")
        self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
    def start_thermal_scenario(self, scenario=None):
        scenario = scenario or ThermalDisturbanceScenario()
        if scenario.inject_tick < self.clock.tick:
            raise ValueError("thermal scenario injection tick is in the past")
        self.scenario = scenario
        self.peer_registry = PeerRegistry(self, scenario.peer_modes)
        self.scenario_applied = False
        for cycle in range(scenario.cycles):
            start = scenario.cycle_start(cycle)
            end = start + scenario.duration_ticks
            self.clock.schedule(start, lambda s=scenario, n=cycle: s.apply(self, n), "thermal_disturbance_start")
            self.clock.schedule(end, lambda s=scenario, n=cycle: s.recover(self, n), "thermal_disturbance_end")
        self.emit("scenario_started", "matrix", inject_tick=scenario.inject_tick)
    def start_equipment_fault(self,scenario):
        """Physical device fault; concurrent faults are allowed and do not replace self.scenario."""
        if not isinstance(scenario,EquipmentFaultScenario): raise TypeError("expected EquipmentFaultScenario")
        if scenario.inject_tick<self.clock.tick: raise ValueError("equipment fault injection tick is in the past")
        self.clock.schedule(scenario.inject_tick,lambda s=scenario:s.apply(self),"equipment_fault_injection")
        if scenario.end_tick is not None:
            self.clock.schedule(scenario.end_tick,lambda s=scenario:s.recover(self),"equipment_fault_recovery")
        self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
        return scenario
    def start_sensor_channel_fault(self,scenario):
        if not isinstance(scenario,SensorChannelFaultScenario): raise TypeError("expected SensorChannelFaultScenario")
        if scenario.inject_tick<self.clock.tick: raise ValueError("sensor fault injection tick is in the past")
        self.clock.schedule(scenario.inject_tick,lambda s=scenario:s.apply(self),"sensor_channel_fault")
        if scenario.end_tick is not None:
            self.clock.schedule(scenario.end_tick,lambda s=scenario:s.recover(self),"sensor_channel_recovery")
        self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
        return scenario
    def start_power_outage(self,scenario=None):
        scenario=scenario or PowerOutageScenario()
        if scenario.inject_tick<self.clock.tick: raise ValueError("outage injection tick is in the past")
        self.clock.schedule(scenario.inject_tick,lambda s=scenario:s.apply(self),"power_outage")
        if scenario.end_tick is not None:
            self.clock.schedule(scenario.end_tick,lambda s=scenario:s.recover(self),"power_restored")
        self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
        return scenario
    def start_water_cut(self,scenario=None):
        scenario=scenario or WaterCutScenario()
        if scenario.inject_tick<self.clock.tick: raise ValueError("water cut injection tick is in the past")
        self.clock.schedule(scenario.inject_tick,lambda s=scenario:s.apply(self),"water_cut")
        if scenario.end_tick is not None:
            self.clock.schedule(scenario.end_tick,lambda s=scenario:s.recover(self),"water_restored")
        self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
        return scenario
    def start_weather_event(self,scenario=None):
        scenario=scenario or WeatherEventScenario()
        if scenario.inject_tick<self.clock.tick: raise ValueError("weather event tick is in the past")
        scenario.schedule(self)
        self.emit("scenario_started","matrix",inject_tick=scenario.inject_tick)
        return scenario
    def monitoring_outputs(self):
        """Per-device operational datasets defined in the device documents ("sent to the monitoring PC")."""
        return {device_id:device.monitoring_output() for device_id,device in self.world.devices.items() if device.online}
    def _scenario_action(self):
        cmd=self.submit_command("incubator-1",self.scenario.action,self.scenario.action_arguments,"scenario")
        if self.scenario.action_fails:self.forced_failures.add(cmd["command_id"])
        if self.scenario.action_unverifiable:self.forced_unverifiable.add(cmd["command_id"])
    def send_message(self,source,target,message_type,payload,delay=0,correlation_id=None,command_id=None):
        ident=f"msg-{self.sequence:06d}"; msg=Message(source,target,message_type,self.clock.time,payload,command_id,correlation_id,ident); accepted=self.transport.send(msg,self.clock.time,delay); self.emit("message_sent",source,target_id=target,message_type=message_type,message_id=ident,timestamp=msg.timestamp,payload=payload,correlation_id=correlation_id,command_id=command_id)
        if not accepted:self.emit("message_dropped",source,target_id=target,message_type=message_type,message_id=ident,correlation_id=correlation_id,command_id=command_id)
        return ident
    def step(self, *, return_snapshot=True):
        if self.paused:return self.snapshot()
        started=time.perf_counter(); self.world.tick=self.clock.tick
        if self.weather: self.weather_now=self.weather.apply(self)
        for event in self.clock.due(): event.callback()
        self.comms.update()
        t=time.perf_counter()
        for device in self.world.devices.values():device.update(self.clock.step_seconds)
        device_time=time.perf_counter()-t
        self.world.devices["animal-protection"].receive_hvac_output(
            self.world.devices["hvac"].output_to_animal_protection()
        )
        self.world.devices["animal-protection"].update(0)
        for controller in self.controllers.values():controller.step(self)
        physical_started=time.perf_counter()
        self.physical_world.integrate(self.world.devices,self.clock.step_seconds)
        self.physical_world.sync_devices(self.world.devices,dt=self.clock.step_seconds)
        if self.active_sensor_fault is not None:
            self.active_sensor_fault.apply_measurement(self)
        self.world.devices["animal-protection"].receive_hvac_output(
            self.world.devices["hvac"].output_to_animal_protection()
        )
        self.world.devices["animal-protection"]._publish(0)
        physical_time=(time.perf_counter()-physical_started)*1000
        publication_started=time.perf_counter()
        self.device_computer.publish()
        for computer in self.device_computers.values():computer.publish()
        publication_time=(time.perf_counter()-publication_started)*1000
        t=time.perf_counter()
        for msg in self.transport.deliver(self.clock.time):
            self.emit("message_received",msg.target_id,message_type=msg.message_type,message_id=msg.message_id,sender_id=msg.source_id,target_id=msg.target_id,timestamp=msg.timestamp,correlation_id=msg.correlation_id,command_id=msg.command_id)
            if msg.message_type=="peer_report":
                pending_query=self.pending_peer_queries.get(msg.source_id)
                matched=bool(pending_query and pending_query["message_id"]==msg.correlation_id)
                self.peer_reports[msg.source_id]=msg.payload
                if matched:self.pending_peer_queries.pop(msg.source_id,None); self.peer_missing.discard(msg.source_id)
                else:self.emit("peer_response_unmatched",msg.target_id,peer_id=msg.source_id,correlation_id=msg.correlation_id,pending_message_id=pending_query["message_id"] if pending_query else None)
                age=self.clock.tick-msg.payload.get("sample_tick",-10**9); fresh=0<=age<=self.peer_freshness_ticks
                self.emit("peer_report",msg.source_id,report=msg.payload,fresh=fresh,correlation_id=msg.correlation_id)
            elif msg.message_type=="command":
                command=self.world.commands.get(msg.command_id)
                if command and command["status"]=="REQUESTED":self._execute_command(command)
                elif command:self.emit("command_ignored",msg.target_id,command_id=msg.command_id,status=command["status"],reason="command is already terminal or acknowledged")
        timeout=float(self.config.get("command_timeout_s",5.0))
        for cmd in self.world.commands.values():
            if cmd["status"]=="REQUESTED" and self.clock.time-cmd["timestamp"]>=timeout:
                cmd["status"]="TIMEOUT"; self.emit("command_state",cmd["target_id"],command_id=cmd["command_id"],status="TIMEOUT")
        for peer_id,query in list(self.pending_peer_queries.items()):
            if self.clock.tick>=query["deadline_tick"]:
                self.pending_peer_queries.pop(peer_id,None)
                self.peer_missing.add(peer_id)
                self.emit("peer_response_missing","jarvis-real",peer_id=peer_id,query_message_id=query["message_id"],query_tick=query["query_tick"])
        delivery_time=time.perf_counter()-t
        for msg in self.jarvis_link.deliver(self.clock.time):
            self.emit("device_interface_message_received",msg.target_id,message_type=msg.message_type,
                      message_id=msg.message_id,sender_id=msg.source_id,correlation_id=msg.correlation_id)
        self.device_computer.publish_command_updates()
        if self.scenario and self.clock.tick>=self.scenario.inject_tick and not self.scenario_applied:
            self.scenario_applied=True; self.peer_registry.query_all(self)
        elif self.scenario_applied:self.peer_registry.query_all(self)
        self.world.tick=self.clock.tick
        evaluation_started=time.perf_counter()
        result=self.evaluator.evaluate(self); self.results.append(result)
        evaluation_time=(time.perf_counter()-evaluation_started)*1000
        tick=self.clock.tick
        self.clock.advance()
        if return_snapshot:
            snapshot_started=time.perf_counter(); snapshot=self.snapshot()
            snapshot_time=(time.perf_counter()-snapshot_started)*1000
        else:
            snapshot=None; snapshot_time=0.0
        self.profile_metrics.append({"tick":tick,"step_ms":(time.perf_counter()-started)*1000,
            "device_update_ms":device_time*1000,"physical_update_ms":physical_time,
            "publication_ms":publication_time,"message_handling_ms":delivery_time*1000,
            "evaluation_ms":evaluation_time,"snapshot_ms":snapshot_time})
        return snapshot
    def snapshot(self):
        observation=self.world.observation(); observation["time_s"]=self.clock.time
        if self.weather:
            hour=self.weather.hour_of_day(self.clock.time); minutes=int(round(hour*60))%1440
            observation["time_of_day"]=f"{minutes//60:02d}:{minutes%60:02d}"
        else:
            observation["time_of_day"]=None
        return observation
    def flush_event_log(self):
        if self._event_stream is not None:self._event_stream.flush()
    def close(self):
        if self._event_stream is not None:
            self._event_stream.flush(); self._event_stream.close(); self._event_stream=None
    def device(self,device_id):
        if device_id not in self.world.devices: raise KeyError(device_id)
        return self.world.devices[device_id].public_telemetry()
    def submit_command(self,target,kind,arguments=None,source="test",delay=0,correlation_id=None):
        if target not in self.world.devices: raise KeyError(target)
        cid=f"cmd-{len(self.world.commands)+1:05d}"; cmd={"command_id":cid,"episode_id":self.world.episode_id,"source_id":source,"target_id":target,"command_type":kind,"arguments":dict(arguments or {}),"timestamp":self.clock.time,"status":"REQUESTED","result":None}; self.world.commands[cid]=cmd
        if correlation_id is not None:cmd["correlation_id"]=correlation_id
        self.emit("command_state",target,command_id=cid,status="REQUESTED"); self.send_message(source,target,"command",cmd,delay=delay,correlation_id=correlation_id,command_id=cid); return dict(cmd)
    def _execute_command(self,cmd):
        try:
            cmd["status"]="ACKNOWLEDGED"; self.emit("command_state",cmd["target_id"],command_id=cmd["command_id"],status="ACKNOWLEDGED")
            if cmd["command_id"] in self.forced_failures:
                self.forced_failures.remove(cmd["command_id"]); cmd["status"]="FAILED"; cmd["result"]={"error":"simulated execution failure"}; self.emit("command_state",cmd["target_id"],command_id=cmd["command_id"],status="FAILED",error=cmd["result"]["error"]); return
            result=self.controllers[cmd["target_id"]].command(cmd["command_type"],cmd["arguments"])
            cmd["status"]="EXECUTED"; cmd["result"]=result; self.emit("command_state",cmd["target_id"],command_id=cmd["command_id"],status="EXECUTED",result=result)
            self.world.devices[cmd["target_id"]].update(0); self.controllers[cmd["target_id"]].step(self); observed=self.device(cmd["target_id"])
            desired=all(observed.get(k)==v for k,v in result.items())
            if cmd["command_id"] in self.forced_unverifiable:self.forced_unverifiable.remove(cmd["command_id"]);desired=False
            cmd["status"]="VERIFIED" if desired else "VERIFICATION_FAILED"; self.emit("command_state",cmd["target_id"],command_id=cmd["command_id"],status=cmd["status"])
        except (ValueError,KeyError) as exc:
            cmd["status"]="REJECTED"; cmd["result"]={"error":str(exc)}; self.emit("command_state",cmd["target_id"],command_id=cmd["command_id"],status="REJECTED",error=str(exc))
