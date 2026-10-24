"""Animal Protection state, configured simulated inputs, and telemetry outputs."""
from dataclasses import dataclass
from ..fastcopy import json_copy

from .base_device import BaseDevice
from .telemetry_utils import capability_value, rate_per_minute


@dataclass
class AnimalProtectionUnit(BaseDevice):
    DEFAULT_CAPABILITIES = {
        "feed_level": True, "feeder_hopper": True, "feed_dispensing_rate": True,
        "feeder_blockage": True, "water_level": True, "water_flow": True,
        "water_pressure": True, "water_pump": True, "water_leak": True,
        "other_gases": True, "carbon_monoxide": True, "smoke": True, "weather_provider": True,
        "wind": True, "rainfall": True, "floodwater": True, "barometric_pressure": True,
        "windows": True, "vents": True, "door_gate": True, "flood_barrier": True,
        "emergency_shutters": True, "closure_feedback": True, "actuator_health": True,
        "backup_battery": True, "backup_power": True, "animal_population": True,
        "animal_activity": True,
    }

    def __post_init__(self):
        self.capabilities={**self.DEFAULT_CAPABILITIES,**self.config.get("capabilities",{})}
        self.weather_provider={"configured":True,"wind_speed_m_s":2.3,"wind_direction_deg":220.0,
            "rainfall_rate_mm_h":0.0,"cumulative_rainfall_mm":0.0,"floodwater_level_cm":0.0,
            "intrusion_detected":False,"external_temperature_c":28.0,"barometric_pressure_hpa":1013.0,
            "severe_weather_warning":False,"tornado_warning":False,"flood_warning":False,
            "regional_hazard_warning":None,"warning_issue_time_s":None,"warning_valid_until_s":None,
            "warning_source":"configured_simulated_weather_provider",
            "weather_watch":None,"watch_issue_time_s":None,"watch_valid_until_s":None}
        self.weather_provider.update(json_copy(self.config.get("weather_provider",{})))
        self.internal={
            "feed_storage_level_pct":75.0,"feed_storage_capacity_kg":50.0,"feed_hopper_levels_pct":{"barn-feeder-1":70.0},
            "feed_consumption_rate_kg_h":.3,"feed_consumption_source":"estimated","cumulative_feed_consumption_kg":0.0,
            "feed_dispensing_command_on":False,"feed_dispensing_on":False,"feed_dispensing_rate_kg_h":0.0,
            "feed_supply_available":True,"feeder_blocked":False,"feed_sensor_health":"good","feed_faults":[],
            "water_level_pct":75.0,"water_capacity_l":1000.0,"water_consumption_rate_l_h":.4,
            "water_consumption_source":"estimated","cumulative_water_consumption_l":0.0,"water_flow_rate_l_min":.4/60,
            "water_pressure_kpa":250.0,"water_supply_available":True,"water_pump_on":True,"water_leak_detected":False,
            "water_sensor_health":"good","water_faults":[],
            "air_quality_by_zone":{"barn":{"co2_ppm":600.0,"o2_pct":20.9,"ammonia_ppm":5.0,"co_ppm":0.0,"other_gases_ppm":{"hydrogen_sulfide_ppm":0.0}}},
            "smoke_obscuration_pct_m":0.0,"smoke_detector_health":"good",
            "smoke_detected":False,"air_quality_sensor_health":"good","air_quality_calibrated":True,
            "windows_pct":30.0,"window_command_pct":30.0,"window_actual_positions_pct":{"north":30.0,"south":30.0},
            "vents_pct":40.0,"vent_command_pct":40.0,"vent_actual_positions_pct":{"barn":40.0},
            "door_gate_positions":{"north_gate":"closed"},"flood_barrier_state":"ready","emergency_shutter_state":"standby",
            "closure_confirmation":{"windows":True,"vents":True},"actuator_health":{"windows":"good","vents":"good"},
            "actuator_power_available":True,"actuator_response_time_s":{"windows":0.8,"vents":0.7},
            "emergency_ready":True,"backup_battery_charge_pct":100.0,"backup_power_available":True,
            "mains_power_status":"normal","alarm_notification_status":"ready","connected_equipment_status":{},
            "building_faults":[],"species":"chicken","housing_zone_id":"barn","population_count":24,
            "animal_activity":{"activity_index_pct":65.0},"inactivity_alert":False,"controller_mode":"automatic","emergency_mode":False,
            "emergency_duration_s":0.0,"time_since_hazard_s":None,"overall_protection_state":"normal",
            "alarms":[],"faults":[],"required_protective_actions":[],"requested_actions":[],"safety_conflicts":[],
            "environment":{"temperature_c":24.0,"humidity_pct":55.0},"weather_warning":None,"flood":False,
            "aggregate_welfare_index_pct":100.0,
            "uptime_s":0.0,"last_successful_communication_s":0.0,"last_hvac_measurement_s":None,
            "time_above_gas_limits_s":{},"time_below_o2_limits_s":{},"time_outside_environment_limits_s":{},
        }
        self.internal.update(json_copy(self.config.get("initial_internal",{})))
        # Configured or test-overridden positions become the actuators' starting point.
        i=self.internal
        if "window_command_pct" not in self.config.get("initial_internal",{}):
            i["window_command_pct"]=float(i["windows_pct"])
        if "vent_command_pct" not in self.config.get("initial_internal",{}):
            i["vent_command_pct"]=float(i["vents_pct"])
        if "window_actual_positions_pct" not in self.config.get("initial_internal",{}):
            i["window_actual_positions_pct"]={k:float(i["windows_pct"]) for k in i["window_actual_positions_pct"]}
        if "vent_actual_positions_pct" not in self.config.get("initial_internal",{}):
            i["vent_actual_positions_pct"]={k:float(i["vents_pct"]) for k in i["vent_actual_positions_pct"]}
        # Physical faults injected by Matrix scenarios (private to the plant).
        i.setdefault("hidden_faults",{})
        i.setdefault("ac_voltage_v",230.0)
        i.setdefault("feed_hopper_capacity_kg",{name:5.0 for name in i["feed_hopper_levels_pct"]})
        i.setdefault("feed_hopper_kg",{name:i["feed_hopper_capacity_kg"][name]*float(pct)/100.0 for name,pct in i["feed_hopper_levels_pct"].items()})
        i.setdefault("feed_auger_rate_kg_h",6.0)
        i.setdefault("water_refill_rate_l_h",200.0)
        i.setdefault("water_refill_on",False)
        i.setdefault("water_supply_pressure_kpa",300.0)   # farm water main, measured at the tank inlet
        i.setdefault("water_supply_flow_l_min",0.0)
        i.setdefault("drinker_water_available",True)
        i.setdefault("backup_battery_capacity_wh",500.0)
        i.setdefault("door_gate_commands",{name:state for name,state in i["door_gate_positions"].items()})
        i.setdefault("door_gate_progress",{name:1.0 if state=="closed" else 0.0 for name,state in i["door_gate_positions"].items()})
        i.setdefault("flood_barrier_command","retracted"); i.setdefault("flood_barrier_progress",0.0)
        i.setdefault("emergency_shutter_command","retracted"); i.setdefault("emergency_shutter_progress",0.0)
        i.setdefault("alarm_manual_activation",False)
        i.setdefault("pressure_history",[])
        i.setdefault("pressure_tendency_hpa_3h",0.0)
        i.setdefault("code_since_s",{})
        self._diagnostic_timers={}
        if self.capabilities["animal_activity"] and self.internal.get("animal_monitoring_sensor_health") is None:
            self.internal["animal_monitoring_sensor_health"]="good"
        self.hvac_input=None
        self._previous_air_quality={}
        self.update(0.0)

    def receive_hvac_output(self,payload):
        self.hvac_input=json_copy(payload)
        self.internal["last_hvac_measurement_s"]=payload.get("measurement_timestamp_s")
        temperatures=[v for v in payload.get("barn_temperature_sensors_c") or [] if v is not None]
        humidities=[v for v in payload.get("barn_humidity_sensors_pct") or [] if v is not None]
        if temperatures and humidities:
            self.internal["environment"]={
                "temperature_c":sum(temperatures)/len(temperatures),
                "humidity_pct":sum(humidities)/len(humidities),
            }
        self.internal["connected_equipment_status"]["hvac"]="online" if payload.get("online") else "offline"

    def update(self,dt):
        i=self.internal; dt=float(dt); i["uptime_s"]+=dt
        self._move_actuators(dt)
        self._power(dt)
        self._feed(dt)
        self._water(dt)
        self._building(dt)
        self._weather_feed(dt)
        if not self.capabilities["weather_provider"] or not self.weather_provider.get("configured",False):
            i["weather_warning"]=None
            i["flood"]=None
        else:
            active=[k for k in ("severe_weather_warning","tornado_warning","flood_warning") if self.weather_provider.get(k)]
            i["weather_warning"]=";".join(active) if active else None
            # Standing water counts as a flood from 5 cm, or as soon as water enters the barn.
            i["flood"]=bool((self.weather_provider.get("floodwater_level_cm") or 0)>=5.0 or self.weather_provider.get("intrusion_detected"))
        if i["flood"] or i["weather_warning"]: i["emergency_ready"]=True
        self._diagnose(dt)
        i["emergency_duration_s"]+=dt if i["emergency_mode"] else 0
        hazard_now=bool(i["flood"] or i["weather_warning"] or i["faults"] or i["alarms"] or i["building_faults"])
        if hazard_now:
            i["time_since_hazard_s"]=0.0 if i["time_since_hazard_s"] is None else i["time_since_hazard_s"]+dt
        else:
            i["time_since_hazard_s"]=None
        for zone,values in i["air_quality_by_zone"].items():
            thresholds=self.config.get("limits",{})
            if values.get("co2_ppm") is not None and values["co2_ppm"]>thresholds.get("co2_max_ppm",5000): i["time_above_gas_limits_s"][zone]=i["time_above_gas_limits_s"].get(zone,0)+dt
            else:i["time_above_gas_limits_s"].setdefault(zone,0.0)
            if values.get("o2_pct") is not None and values["o2_pct"]<thresholds.get("o2_min_pct",19.5): i["time_below_o2_limits_s"][zone]=i["time_below_o2_limits_s"].get(zone,0)+dt
            else:i["time_below_o2_limits_s"].setdefault(zone,0.0)
            environment=i["environment"]
            temp=environment.get("temperature_c"); humidity=environment.get("humidity_pct")
            outside=(temp is not None and not thresholds.get("temperature_min_c",0)<=temp<=thresholds.get("temperature_max_c",45)) or (humidity is not None and not thresholds.get("humidity_min_pct",0)<=humidity<=thresholds.get("humidity_max_pct",100))
            i["time_outside_environment_limits_s"][zone]=i["time_outside_environment_limits_s"].get(zone,0.0)+(dt if outside else 0.0)
        if self.hvac_input:
            t=self.hvac_input.get("measurement_timestamp_s")
            i["environment_freshness_s"]=max(0.0,i["uptime_s"]-t) if t is not None else None
        else:i["environment_freshness_s"]=None
        self._publish(dt)

    def _set_code(self,kind,code,active):
        i=self.internal; codes=i[kind]
        if active and code not in codes:
            codes.append(code); i["code_since_s"][code]=i["uptime_s"]
        elif not active and code in codes:
            codes.remove(code); i["code_since_s"].pop(code,None)

    def _persist(self,name,condition,dt,after_s):
        timer=self._diagnostic_timers.get(name,0.0)+dt if condition else 0.0
        self._diagnostic_timers[name]=timer
        return condition and timer>=after_s

    def _power(self,dt):
        """Mains supply and the backup battery that keeps controller and actuators alive."""
        i=self.internal
        mains_ok=float(i["ac_voltage_v"])>=180.0
        i["mains_power_status"]="normal" if mains_ok else "outage"
        battery_ok=self.capabilities["backup_battery"] and float(i["backup_battery_charge_pct"])>0.0
        capacity=max(1.0,float(i["backup_battery_capacity_wh"]))
        moving=any(abs(float(v)-float(i["window_command_pct"]))>0.5 for v in i["window_actual_positions_pct"].values()) or \
            any(abs(float(v)-float(i["vent_command_pct"]))>0.5 for v in i["vent_actual_positions_pct"].values()) or \
            i["flood_barrier_state"] in ("deploying","retracting") or i["emergency_shutter_state"] in ("deploying","retracting")
        if not mains_ok and battery_ok:
            load_w=15.0+(120.0 if moving else 0.0)
            i["backup_battery_charge_pct"]=max(0.0,float(i["backup_battery_charge_pct"])-load_w/0.9*dt/3600.0/capacity*100.0)
        elif mains_ok and self.capabilities["backup_battery"]:
            i["backup_battery_charge_pct"]=min(100.0,float(i["backup_battery_charge_pct"])+50.0*dt/3600.0/capacity*100.0)
        i["backup_power_available"]=bool(self.capabilities["backup_power"] and float(i["backup_battery_charge_pct"])>0.0)
        powered=mains_ok or (self.capabilities["backup_battery"] and float(i["backup_battery_charge_pct"])>0.0)
        i["actuator_power_available"]=powered
        self.online=powered

    def _feed(self,dt):
        """Silo -> auger -> hoppers -> birds. Birds eat only from the hoppers."""
        i=self.internal; hidden=i["hidden_faults"]
        capacity=i["feed_hopper_capacity_kg"]; hoppers=i["feed_hopper_kg"]
        storage=max(0.0,i["feed_storage_capacity_kg"]*i["feed_storage_level_pct"]/100.0)
        with_feed=[name for name,kg in hoppers.items() if kg>0.0]
        eaten=min(float(i["feed_consumption_rate_kg_h"])*dt/3600.0,sum(hoppers.values()))
        for name in with_feed:
            hoppers[name]=max(0.0,hoppers[name]-eaten/len(with_feed))
        fill=[name for name in hoppers if hoppers[name]<0.4*capacity[name]]
        full=all(hoppers[name]>=0.9*capacity[name] for name in hoppers)
        command=i["feed_dispensing_command_on"]
        if fill and storage>0.0: command=True
        elif full or storage<=0.0: command=False
        motor=bool(command and i["mains_power_status"]=="normal")
        flow_kg=0.0 if (hidden.get("feeder_blocked") or not motor) else min(storage,float(i["feed_auger_rate_kg_h"])*dt/3600.0)
        targets=[name for name in hoppers if hoppers[name]<capacity[name]] or list(hoppers)
        for name in targets:
            hoppers[name]=min(capacity[name],hoppers[name]+flow_kg/len(targets))
        storage-=flow_kg
        i["feed_dispensing_command_on"]=command
        i["feed_dispensing_on"]=motor
        i["feed_dispensing_rate_kg_h"]=round(flow_kg*3600.0/dt,4) if dt>0 else 0.0
        i["cumulative_feed_consumption_kg"]+=eaten
        if self.capabilities["feed_level"]:
            i["feed_storage_level_pct"]=storage/max(i["feed_storage_capacity_kg"],.001)*100
        i["feed_hopper_levels_pct"]={name:round(100.0*hoppers[name]/max(capacity[name],.001),4) for name in hoppers}
        i["feed_supply_available"]=storage>0.0

    def _water(self,dt):
        """Tank -> pump -> pressurised drinker lines; float valve refills from the mains."""
        i=self.internal; hidden=i["hidden_faults"]
        tank_l=max(0.0,i["water_capacity_l"]*i["water_level_pct"]/100.0)
        mains_water=not hidden.get("mains_water_cut")
        i["water_supply_available"]=mains_water
        i["water_supply_pressure_kpa"]=300.0 if mains_water else 0.0
        runs=i["mains_power_status"]=="normal" and tank_l>0.0 and not hidden.get("water_pump")
        leak_l_h=max(0.0,float(hidden.get("water_leak_l_h",0.0))) if runs else 0.0
        i["water_pressure_kpa"]=round(max(0.0,250.0-0.5*leak_l_h),2) if runs else 0.0
        i["drinker_water_available"]=runs and i["water_pressure_kpa"]>80.0
        drink_l_h=float(i["water_consumption_rate_l_h"]) if i["drinker_water_available"] else 0.0
        used=min(tank_l,(drink_l_h+leak_l_h)*dt/3600.0)
        if i["water_level_pct"]<60.0 and mains_water: i["water_refill_on"]=True
        elif i["water_level_pct"]>=95.0 or not mains_water: i["water_refill_on"]=False
        # The fill valve opens on command; a stuck valve passes nothing even with pressure on the main.
        refill=float(i["water_refill_rate_l_h"])*dt/3600.0 if i["water_refill_on"] and not hidden.get("tank_fill_valve") else 0.0
        i["water_supply_flow_l_min"]=round(refill*60.0/dt,4) if dt>0 else 0.0
        tank_l=min(i["water_capacity_l"],tank_l-used+refill)
        i["cumulative_water_consumption_l"]+=min(used,drink_l_h*dt/3600.0)
        if self.capabilities["water_level"]:
            i["water_level_pct"]=tank_l/max(i["water_capacity_l"],.001)*100
        i["water_pump_on"]=runs
        i["water_flow_rate_l_min"]=round((drink_l_h+leak_l_h)/60.0,5)
        if self.capabilities["water_leak"]:
            i["water_leak_detected"]=leak_l_h>0.0

    def _building(self,dt):
        """Gates, flood barrier, and storm shutters travel to their commanded positions."""
        i=self.internal
        if not i["actuator_power_available"]:
            return
        for name,command in i["door_gate_commands"].items():
            target=1.0 if command=="closed" else 0.0
            progress=float(i["door_gate_progress"].get(name,target))
            progress=target if abs(target-progress)<=dt/20.0 else progress+dt/20.0*(1 if target>progress else -1)
            i["door_gate_progress"][name]=progress
            i["door_gate_positions"][name]="closed" if progress>=1.0 else "open" if progress<=0.0 else "moving"
        for kind,stroke,idle in (("flood_barrier",120.0,"ready"),("emergency_shutter",60.0,"standby")):
            if not self.capabilities["flood_barrier" if kind=="flood_barrier" else "emergency_shutters"]:
                continue
            target=1.0 if i[f"{kind}_command"]=="deployed" else 0.0
            progress=float(i[f"{kind}_progress"])
            progress=target if abs(target-progress)<=dt/stroke else progress+dt/stroke*(1 if target>progress else -1)
            i[f"{kind}_progress"]=progress
            i[f"{kind}_state"]=("deployed" if progress>=1.0 else idle if progress<=0.0
                                else "deploying" if target>progress else "retracting")
        i["closure_confirmation"].update({
            "gates":all(i["door_gate_positions"][n]==c for n,c in i["door_gate_commands"].items()),
            "flood_barrier":(i["flood_barrier_progress"]>=1.0)==(i["flood_barrier_command"]=="deployed"),
            "emergency_shutters":(i["emergency_shutter_progress"]>=1.0)==(i["emergency_shutter_command"]=="deployed")})

    def _weather_feed(self,dt):
        """Warning-feed expiry and local pressure tendency."""
        i=self.internal; w=self.weather_provider; now=i["uptime_s"]
        if w.get("warning_valid_until_s") is not None and now>float(w["warning_valid_until_s"]):
            for key in ("severe_weather_warning","tornado_warning","flood_warning"): w[key]=False
            w["regional_hazard_warning"]=None; w["warning_issue_time_s"]=None; w["warning_valid_until_s"]=None
        if w.get("watch_valid_until_s") is not None and now>float(w["watch_valid_until_s"]):
            w["weather_watch"]=None; w["watch_issue_time_s"]=None; w["watch_valid_until_s"]=None
        pressure=w.get("barometric_pressure_hpa")
        history=i["pressure_history"]
        if pressure is not None and dt>0 and (not history or now-history[-1][0]>=60.0):
            history.append((now,float(pressure)))
            while history and now-history[0][0]>3*3600.0: history.pop(0)
        if len(history)>=2:
            # Barometric tendency: change over the last three hours (or the history available).
            i["pressure_tendency_hpa_3h"]=round(history[-1][1]-history[0][1],2)

    def disaster_risk(self):
        """normal / watch / warning / emergency, from the warning feed plus local measurements.

        Local wind alone never establishes a tornado: it can only raise a watch.
        """
        i=self.internal; w=self.weather_provider
        if not (self.capabilities["weather_provider"] and w.get("configured")): return "unknown"
        flood=float(w.get("floodwater_level_cm") or 0.0)
        if w.get("tornado_warning") or w.get("intrusion_detected") or flood>=30.0: return "emergency"
        if w.get("severe_weather_warning") or w.get("flood_warning") or flood>=10.0: return "warning"
        if (w.get("weather_watch") or float(i.get("pressure_tendency_hpa_3h") or 0.0)<=-3.0
                or float(w.get("wind_speed_m_s") or 0.0)>=17.0 or float(w.get("rainfall_rate_mm_h") or 0.0)>=30.0):
            return "watch"
        return "normal"

    def _diagnose(self,dt):
        i=self.internal
        if not self.online:
            return
        blocked=self._persist("feeder",i["feed_dispensing_on"] and i["feed_dispensing_rate_kg_h"]<=0.0,dt,300.0)
        if blocked: i["feeder_blocked"]=True
        elif i["feed_dispensing_rate_kg_h"]>0.0: i["feeder_blocked"]=False
        for kind in ("faults","feed_faults"): self._set_code(kind,"FEEDER_BLOCKED",i["feeder_blocked"])
        self._set_code("alarms","FEED_EMPTY",self.capabilities["feed_level"] and i["feed_storage_level_pct"]<=0.0)
        pump_dead=self._persist("pump",i["mains_power_status"]=="normal" and i["water_level_pct"]>0 and i["water_pressure_kpa"]<50.0,dt,30.0)
        for kind in ("faults","water_faults"): self._set_code(kind,"WATER_PUMP_FAULT",pump_dead)
        supply_lost=self._persist("supply",float(i["water_supply_pressure_kpa"])<50.0,dt,30.0)
        self._set_code("alarms","WATER_SUPPLY_LOST",supply_lost)
        fill_dead=self._persist("fill",i["water_refill_on"] and not supply_lost and float(i["water_supply_flow_l_min"])<=0.0,dt,60.0)
        for kind in ("faults","water_faults"): self._set_code(kind,"TANK_FILL_FAULT",fill_dead)
        self._set_code("alarms","WATER_LEAK",bool(i["water_leak_detected"]))
        self._set_code("alarms","MAINS_POWER_FAILURE",i["mains_power_status"]!="normal")
        self._set_code("alarms","BACKUP_BATTERY_LOW",i["mains_power_status"]!="normal" and float(i["backup_battery_charge_pct"])<20.0)
        self._set_code("alarms","WATER_INTRUSION",bool(self.weather_provider.get("intrusion_detected")))
        critical={"SMOKE_DETECTED","CO_HIGH","O2_LOW","WATER_INTRUSION"}
        sounding=bool(critical.intersection(i["alarms"])) or self.disaster_risk()=="emergency" or i["alarm_manual_activation"]
        i["alarm_notification_status"]="active" if sounding else "ready"

    def _move_actuators(self,dt):
        """Window and vent drives travel at a finite rate toward their commands."""
        i=self.internal
        if not i.get("actuator_power_available",True):
            return
        for command_key,positions_key,summary_key,stroke_s,health in (
                ("window_command_pct","window_actual_positions_pct","windows_pct",60.0,"windows"),
                ("vent_command_pct","vent_actual_positions_pct","vents_pct",30.0,"vents")):
            if i["actuator_health"].get(health,"good")!="good":
                continue
            command=max(0.0,min(100.0,float(i[command_key]))); travel=100.0/stroke_s*dt
            for name,position in i[positions_key].items():
                position=float(position)
                i[positions_key][name]=command if abs(command-position)<=travel else position+travel*(1 if command>position else -1)
            values=list(i[positions_key].values())
            i[summary_key]=round(sum(values)/len(values),3) if values else command
            i["closure_confirmation"][health]=all(abs(v-command)<0.5 for v in values)

    def apply_detector_readings(self,obscuration_pct_m):
        """Local detector logic: smoke and gas alarms with hysteresis."""
        i=self.internal; limits=self.config.get("limits",{})
        alarms=i["alarms"]
        def latch(code,on,off):
            if on and code not in alarms: alarms.append(code); i["code_since_s"][code]=i["uptime_s"]
            elif off and code in alarms: alarms.remove(code); i["code_since_s"].pop(code,None)
        if obscuration_pct_m is None:
            i["smoke_detector_health"]="failed"; i["smoke_obscuration_pct_m"]=None
            if "SMOKE_DETECTOR_FAILED" not in i["faults"]: i["faults"].append("SMOKE_DETECTOR_FAILED")
        else:
            i["smoke_obscuration_pct_m"]=obscuration_pct_m
            threshold=float(self.config.get("smoke_alarm_obscuration_pct_m",8.0))
            if obscuration_pct_m>=threshold: i["smoke_detected"]=True
            elif obscuration_pct_m<threshold*0.5: i["smoke_detected"]=False
        latch("SMOKE_DETECTED",i["smoke_detected"],not i["smoke_detected"])
        barn=i["air_quality_by_zone"].get("barn",{})
        co=barn.get("co_ppm"); nh3=barn.get("ammonia_ppm"); co2=barn.get("co2_ppm"); o2=barn.get("o2_pct")
        temperature=i["environment"].get("temperature_c")
        if co is not None: latch("CO_HIGH",co>=limits.get("co_alarm_ppm",35.0),co<limits.get("co_alarm_ppm",35.0)-10.0)
        if nh3 is not None: latch("AMMONIA_HIGH",nh3>=limits.get("ammonia_alarm_ppm",25.0),nh3<limits.get("ammonia_alarm_ppm",25.0)-5.0)
        if co2 is not None: latch("CO2_HIGH",co2>=limits.get("co2_alarm_ppm",3000.0),co2<limits.get("co2_alarm_ppm",3000.0)-500.0)
        if o2 is not None: latch("O2_LOW",o2<limits.get("o2_alarm_pct",19.5),o2>limits.get("o2_alarm_pct",19.5)+0.3)
        if temperature is not None: latch("HEAT_STRESS",temperature>=limits.get("heat_stress_c",30.0),temperature<limits.get("heat_stress_c",30.0)-2.0)

    def output_to_monitoring_pc(self):
        return json_copy(self.telemetry)

    def output_requests(self):
        return {"supported": {
            "animal-protection": ["set_vents", "set_windows", "set_gate", "deploy_flood_barrier", "deploy_shutters", "activate_alarm"],
            "hvac": ["set_cooling", "set_fan", "emergency_ventilation", "set_mode"],
        }, "unsupported_or_unconfigured": {
            "feed_water_equipment":"automatic local control only; no command interface",
            "backup_power":"automatic changeover only; no command interface",
        }, "requested_actions":json_copy(self.internal["requested_actions"])}

    def _publish(self,dt=0.0):
        i=self.internal; av={}; w=self.weather_provider
        feed_level=capability_value(self.capabilities,"feed_level",i["feed_storage_level_pct"],av)
        feed_health=capability_value({"feed_sensor_health":self.capabilities["feed_level"]},"feed_sensor_health",i["feed_sensor_health"],av,"feed-level sensor is not installed")
        hopper=capability_value(self.capabilities,"feeder_hopper",json_copy(i["feed_hopper_levels_pct"]),av)
        feed_dispense_rate=capability_value(self.capabilities,"feed_dispensing_rate",i["feed_dispensing_rate_kg_h"],av)
        blockage=capability_value(self.capabilities,"feeder_blockage",i["feeder_blocked"],av)
        water_level=capability_value(self.capabilities,"water_level",i["water_level_pct"],av)
        water_health=capability_value({"water_sensor_health":self.capabilities["water_level"]},"water_sensor_health",i["water_sensor_health"],av,"water-level sensor is not installed")
        water_flow=capability_value(self.capabilities,"water_flow",i["water_flow_rate_l_min"],av)
        water_pressure=capability_value(self.capabilities,"water_pressure",i["water_pressure_kpa"],av)
        pump=capability_value(self.capabilities,"water_pump",i["water_pump_on"],av)
        leak=capability_value(self.capabilities,"water_leak",i["water_leak_detected"],av)
        other_gases=capability_value(self.capabilities,"other_gases",{zone:v.get("other_gases_ppm",{}) for zone,v in i["air_quality_by_zone"].items()},av)
        smoke=capability_value(self.capabilities,"smoke",i["smoke_detected"],av)
        weather_available=bool(self.capabilities["weather_provider"] and w.get("configured"))
        av["weather_provider"]={"status":"available","reason":None} if weather_available else {"status":"unavailable","reason":"simulated weather provider is not configured"}
        weather_values={}
        for key,cap,source in (("wind_speed_m_s","wind","wind_speed_m_s"),("wind_direction_deg","wind","wind_direction_deg"),
                               ("rainfall_rate_mm_h","rainfall","rainfall_rate_mm_h"),("cumulative_rainfall_mm","rainfall","cumulative_rainfall_mm"),
                               ("floodwater_level_cm","floodwater","floodwater_level_cm"),("barometric_pressure_hpa","barometric_pressure","barometric_pressure_hpa")):
            enabled=self.capabilities["weather_provider"] and self.capabilities.get(cap,True) and w.get("configured",False)
            weather_values[key]=capability_value({key:enabled},key,w.get(source),av,"weather provider or sensor is not configured")
        for name in ("water_intrusion_detected","external_temperature_c","severe_weather_warning","tornado_warning","flood_warning","regional_hazard_warning","warning_issue_time_s","warning_valid_until_s","warning_source"):
            available=weather_available
            av[name]={"status":"available","reason":None} if available else {"status":"unavailable","reason":"simulated weather provider is not configured"}
        windows=capability_value(self.capabilities,"windows",json_copy(i["window_actual_positions_pct"]),av)
        vents=capability_value(self.capabilities,"vents",json_copy(i["vent_actual_positions_pct"]),av)
        door_gate=capability_value(self.capabilities,"door_gate",json_copy(i["door_gate_positions"]),av)
        flood_barrier=capability_value(self.capabilities,"flood_barrier",i["flood_barrier_state"],av,"flood barrier is not installed")
        shutters=capability_value(self.capabilities,"emergency_shutters",i["emergency_shutter_state"],av)
        closure=capability_value(self.capabilities,"closure_feedback",json_copy(i["closure_confirmation"]),av)
        actuator_health=capability_value(self.capabilities,"actuator_health",json_copy(i["actuator_health"]),av)
        battery=capability_value(self.capabilities,"backup_battery",i["backup_battery_charge_pct"],av,"backup battery is not installed")
        backup=capability_value(self.capabilities,"backup_power",i["backup_power_available"],av,"backup power is not installed")
        population=capability_value(self.capabilities,"animal_population",i["population_count"],av,"population sensor/configuration unavailable")
        activity=capability_value(self.capabilities,"animal_activity",i["animal_activity"],av,"animal activity sensors are not installed")
        animal_sensor_health=capability_value(self.capabilities,"animal_activity",i.get("animal_monitoring_sensor_health"),av,"animal monitoring sensors are not installed")
        env=json_copy(i["environment"]); zones=i["air_quality_by_zone"]
        co2={z:v.get("co2_ppm") for z,v in zones.items()}; o2={z:v.get("o2_pct") for z,v in zones.items()}; ammonia={z:v.get("ammonia_ppm") for z,v in zones.items()}
        carbon_monoxide=capability_value(self.capabilities,"carbon_monoxide",{z:v.get("co_ppm") for z,v in zones.items()},av,"carbon monoxide sensor is not installed")
        weather_known=bool(self.capabilities["weather_provider"] and w.get("configured"))
        feed_supply_status=None if feed_level is None else "empty" if feed_level<=0 else "low" if feed_level<=self.config.get("feed_low_threshold_pct",15.0) else "available"
        water_status=None if water_level is None else "unavailable" if water_level<=0 else "low" if water_level<=self.config.get("water_low_threshold_pct",15.0) else "available"
        protection="emergency" if i["emergency_mode"] or i["flood"] or i["weather_warning"] else "warning" if i["faults"] or i["alarms"] else "normal" if weather_known else "degraded"
        i["overall_protection_state"]=protection
        limits=self.config.get("limits",{})
        if any(v.get("co2_ppm") is None or v.get("o2_pct") is None for v in zones.values()): safety="unknown"
        elif any(v.get("co2_ppm",0)>limits.get("co2_critical_ppm",10000) or v.get("o2_pct",100)<limits.get("o2_critical_pct",18.0) for v in zones.values()): safety="critical"
        elif i["alarms"]: safety="warning"
        else: safety="normal"
        hvac=self.hvac_input or {}
        telemetry={
            "device_id":self.device_id,"online":self.online,"feed_available":None if feed_supply_status is None else feed_supply_status!="empty","water_available":None if water_status is None else water_status!="unavailable",
            "co2_ppm":next(iter(co2.values()),None),"o2_pct":next(iter(o2.values()),None),"ammonia_ppm":next(iter(ammonia.values()),None),
            "environment":env,"weather_warning":i["weather_warning"],"flood":i["flood"],"windows_pct":i["windows_pct"],
            "vents_pct":i["vents_pct"],"emergency_ready":i["emergency_ready"],"alarms":list(i["alarms"]),
            "faults":list(i["faults"]),"health":"fault" if i["faults"] else "warning" if i["alarms"] else "normal",
            "feed_storage_level_pct":feed_level,"feed_storage_remaining_kg":round(i["feed_storage_capacity_kg"]*i["feed_storage_level_pct"]/100,4) if feed_level is not None else None,
            "feed_hopper_levels_pct":hopper,"feed_consumption_rate_kg_h":i["feed_consumption_rate_kg_h"],
            "feed_consumption_source":i["feed_consumption_source"],"cumulative_feed_consumption_kg":round(i["cumulative_feed_consumption_kg"],6),
            "feed_dispensing_command_on":i["feed_dispensing_command_on"],"feed_dispensing_state":"fault" if i["feeder_blocked"] else "on" if i["feed_dispensing_on"] else "off",
            "feed_dispensing_rate_kg_h":feed_dispense_rate,"feed_supply_status":feed_supply_status,
            "feeder_blocked":blockage,"feed_sensor_health":feed_health,"feed_faults":list(i["feed_faults"]),
            "water_tank_level_pct":water_level,"water_tank_volume_l":round(i["water_capacity_l"]*i["water_level_pct"]/100,4) if water_level is not None else None,
            "water_consumption_rate_l_h":i["water_consumption_rate_l_h"],"water_consumption_source":i["water_consumption_source"],
            "cumulative_water_consumption_l":round(i["cumulative_water_consumption_l"],6),"water_flow_rate_l_min":water_flow,
            "water_pressure_kpa":water_pressure,"water_availability_status":water_status,
            "water_pump_state":"on" if pump else "off" if pump is not None else None,"water_leak_detected":leak,
            "water_sensor_health":water_health,"water_faults":list(i["water_faults"]),
            "co2_ppm_by_zone":co2,"o2_pct_by_zone":o2,"ammonia_ppm_by_zone":ammonia,"other_gases_ppm_by_zone":other_gases,
            "co_ppm_by_zone":carbon_monoxide,"co_ppm":next(iter(carbon_monoxide.values()),None) if carbon_monoxide else None,
            "smoke_obscuration_pct_m":capability_value(self.capabilities,"smoke",i.get("smoke_obscuration_pct_m"),{}),
            "smoke_detector_health":i.get("smoke_detector_health","good") if self.capabilities["smoke"] else None,
            "smoke_detected":smoke,"environment_temperature_c":env.get("temperature_c"),"environment_humidity_pct":env.get("humidity_pct"),
            "hvac_input":json_copy(hvac),"air_quality_sensor_health":i["air_quality_sensor_health"],"air_quality_calibrated":i["air_quality_calibrated"],
            "air_quality_measurement_timestamp_s":i["last_hvac_measurement_s"],"time_above_gas_limits_s":json_copy(i["time_above_gas_limits_s"]),
            "time_below_o2_limits_s":json_copy(i["time_below_o2_limits_s"]),"time_outside_environment_limits_s":json_copy(i["time_outside_environment_limits_s"]),
            "air_quality_trend_ppm_min":{zone:rate_per_minute(vals.get("co2_ppm"),self._previous_air_quality.get(zone),dt) for zone,vals in zones.items()},
            "environmental_safety_state":safety,
            "aggregate_welfare_index_pct":round(float(i.get("aggregate_welfare_index_pct", 100.0)), 4),
            "wind_speed_m_s":weather_values["wind_speed_m_s"],"wind_direction_deg":weather_values["wind_direction_deg"],
            "rainfall_rate_mm_h":weather_values["rainfall_rate_mm_h"],"cumulative_rainfall_mm":weather_values["cumulative_rainfall_mm"],
            "floodwater_level_cm":weather_values["floodwater_level_cm"],"water_intrusion_detected":w.get("intrusion_detected") if weather_available else None,
            "external_temperature_c":w.get("external_temperature_c") if weather_available else None,
            "barometric_pressure_hpa":weather_values["barometric_pressure_hpa"],
            "severe_weather_warning":w.get("severe_weather_warning") if weather_available else None,
            "tornado_warning":w.get("tornado_warning") if weather_available else None,"flood_warning":w.get("flood_warning") if weather_available else None,
            "regional_hazard_warning":w.get("regional_hazard_warning") if weather_available else None,
            "warning_issue_time_s":w.get("warning_issue_time_s") if weather_available else None,
            "warning_valid_until_s":w.get("warning_valid_until_s") if weather_available else None,
            "warning_source":w.get("warning_source") if weather_available else None,
            "disaster_risk_state":self.disaster_risk(),
            "weather_watch":w.get("weather_watch") if weather_available else None,
            "watch_issue_time_s":w.get("watch_issue_time_s") if weather_available else None,
            "watch_valid_until_s":w.get("watch_valid_until_s") if weather_available else None,
            "pressure_tendency_hpa_3h":i.get("pressure_tendency_hpa_3h") if weather_available else None,
            "flood_barrier_command":i["flood_barrier_command"],"emergency_shutter_command":i["emergency_shutter_command"],
            "door_gate_commands":json_copy(i["door_gate_commands"]),"water_tank_refill_on":i["water_refill_on"],
            "mains_water_supply_available":i["water_supply_available"],"drinker_water_pressure_ok":i["drinker_water_available"],
            "water_supply_pressure_kpa":i["water_supply_pressure_kpa"],"water_supply_flow_l_min":i["water_supply_flow_l_min"],
            "active_faults":[{"code":c,"severity":"critical","detected_at_s":i["code_since_s"].get(c,i["uptime_s"])} for c in i["faults"]],
            "active_alarms":[{"code":c,"severity":"critical" if c in ("SMOKE_DETECTED","CO_HIGH","O2_LOW","WATER_INTRUSION") else "warning",
                              "detected_at_s":i["code_since_s"].get(c,i["uptime_s"])} for c in i["alarms"]],
            "window_positions_pct":windows,"window_command_pct":i["window_command_pct"],"window_actual_positions_pct":windows,
            "vent_positions_pct":vents,"vent_command_pct":i["vent_command_pct"],"vent_actual_positions_pct":vents,
            "door_gate_positions":door_gate,"flood_barrier_state":flood_barrier,"emergency_shutter_state":shutters,
            "closure_confirmation":closure,"actuator_health":actuator_health,"actuator_power_available":i["actuator_power_available"],
            "actuator_response_time_s":json_copy(i["actuator_response_time_s"]),"emergency_equipment_ready":i["emergency_ready"],
            "backup_battery_charge_pct":battery,"backup_power_available":backup,"mains_power_status":i["mains_power_status"],
            "alarm_notification_status":i["alarm_notification_status"],"connected_equipment_status":json_copy(i["connected_equipment_status"]),
            "building_faults":list(i["building_faults"]),"species":i["species"],"housing_zone_id":i["housing_zone_id"],
            "population_count":population,"animal_activity":activity,"inactivity_alert":i["inactivity_alert"],
            "animal_monitoring_sensor_health":animal_sensor_health,
            "feed_availability_duration_h":round(i["feed_storage_capacity_kg"]*i["feed_storage_level_pct"]/100/max(i["feed_consumption_rate_kg_h"],.001),3) if feed_level is not None else None,
            "water_availability_duration_h":round(i["water_capacity_l"]*i["water_level_pct"]/100/max(i["water_consumption_rate_l_h"],.001),3) if water_level is not None else None,
            "overall_protection_state":protection,"animal_welfare_alerts":list(i["alarms"]),
            "active_emergency_conditions":[{"code":"FLOOD" if i["flood"] else "WEATHER_WARNING","severity":"critical" if i["flood"] else "warning"} for _ in range(1 if i["flood"] or i["weather_warning"] else 0)],
            "required_protective_actions":list(i["required_protective_actions"]),"requested_actions":json_copy(i["requested_actions"]),
            "supported_external_requests":self.output_requests(),"safety_conflicts":list(i["safety_conflicts"]),
            "time_since_hazard_s":i["time_since_hazard_s"],"time_in_emergency_mode_s":i["emergency_duration_s"],
            "data_freshness_s":i.get("environment_freshness_s"),"controller_mode":i["controller_mode"],
            "uptime_s":i["uptime_s"],"last_successful_communication_s":i["last_successful_communication_s"],
            "last_internal_telemetry_update_s":i["uptime_s"],"measurement_availability":av,
            "measurement_sources":{"feed_consumption_rate_kg_h":i["feed_consumption_source"],"water_consumption_rate_l_h":i["water_consumption_source"],"water_flow_rate_l_min":"measured by configured simulated flow sensor",
                "environment":"received from HVAC device computer","weather":"configured simulated weather provider"},
        }
        self.telemetry=telemetry
        if dt>0:self._previous_air_quality={zone:vals.get("co2_ppm") for zone,vals in zones.items()}

    def monitoring_output(self, copy=True):
        """Dataset sent to the monitoring PC (docs: Animal Protection part 2, section 6)."""
        t=(self.public_telemetry() if copy else (self.telemetry or self.public_telemetry())); i=self.internal
        return {
            "device_id":self.device_id,"observed_at_s":i["uptime_s"],
            "food":{"storage_level_pct":t["feed_storage_level_pct"],"hopper_levels_pct":t["feed_hopper_levels_pct"],
                    "supply_status":t["feed_supply_status"],"consumption_kg_h":t["feed_consumption_rate_kg_h"],
                    "dispensing_state":t["feed_dispensing_state"],"feeder_blocked":t["feeder_blocked"],"faults":t["feed_faults"]},
            "water":{"tank_level_pct":t["water_tank_level_pct"],"status":t["water_availability_status"],
                     "flow_l_min":t["water_flow_rate_l_min"],"pressure_kpa":t["water_pressure_kpa"],
                     "consumption_l_h":t["water_consumption_rate_l_h"],"leak_detected":t["water_leak_detected"],"faults":t["water_faults"],
                     "supply_pressure_kpa":t["water_supply_pressure_kpa"],"supply_flow_l_min":t["water_supply_flow_l_min"],
                     "refill_on":t["water_tank_refill_on"]},
            "air_quality":{"co2_ppm":t["co2_ppm_by_zone"],"o2_pct":t["o2_pct_by_zone"],"ammonia_ppm":t["ammonia_ppm_by_zone"],
                           "co_ppm":t["co_ppm_by_zone"],"smoke_detected":t["smoke_detected"],
                           "smoke_obscuration_pct_m":t["smoke_obscuration_pct_m"],"safety_state":t["environmental_safety_state"]},
            "animal_environment":{"temperature_c":t["environment_temperature_c"],"humidity_pct":t["environment_humidity_pct"]},
            "weather":{"external_temperature_c":t["external_temperature_c"],"wind_speed_m_s":t["wind_speed_m_s"],
                       "wind_direction_deg":t["wind_direction_deg"],"rainfall_mm_h":t["rainfall_rate_mm_h"],
                       "pressure_hpa":t["barometric_pressure_hpa"],"pressure_tendency_hpa_3h":t["pressure_tendency_hpa_3h"],
                       "floodwater_level_cm":t["floodwater_level_cm"],"water_intrusion":t["water_intrusion_detected"]},
            "disaster_alerts":{"risk_state":t["disaster_risk_state"],"watch":t["weather_watch"],
                               "severe_weather_warning":t["severe_weather_warning"],"tornado_warning":t["tornado_warning"],
                               "flood_warning":t["flood_warning"],"regional_hazard_warning":t["regional_hazard_warning"],
                               "issued_at_s":t["warning_issue_time_s"],"valid_until_s":t["warning_valid_until_s"],"source":t["warning_source"]},
            "building_protection":{"windows_pct":t["window_actual_positions_pct"],"vents_pct":t["vent_actual_positions_pct"],
                                   "gates":t["door_gate_positions"],"flood_barrier":t["flood_barrier_state"],
                                   "storm_shutters":t["emergency_shutter_state"]},
            "closure_status":{"requested":{"windows_pct":t["window_command_pct"],"vents_pct":t["vent_command_pct"],
                                           "gates":t["door_gate_commands"],"flood_barrier":t["flood_barrier_command"],
                                           "storm_shutters":t["emergency_shutter_command"]},
                              "confirmed":t["closure_confirmation"]},
            "emergency_readiness":{"backup_power_available":t["backup_power_available"],"backup_battery_charge_pct":t["backup_battery_charge_pct"],
                                   "mains_power_status":t["mains_power_status"],"equipment_ready":t["emergency_equipment_ready"],
                                   "alarm_system":t["alarm_notification_status"]},
            "animal_welfare":{"activity":t["animal_activity"],"inactivity_alert":t["inactivity_alert"],
                              "welfare_index_pct":t["aggregate_welfare_index_pct"],"population":t["population_count"]},
            "overall_status":t["overall_protection_state"],"alarms":t["active_faults"]+t["active_alarms"],
            "protective_actions":{"required":t["required_protective_actions"],"requested":t["requested_actions"]},
            "safety_conflicts":t["safety_conflicts"],
            "connectivity":{"online":t["online"],"last_update_s":t["last_internal_telemetry_update_s"]},
        }

    def mark_communication(self,time_s):
        self.internal["last_successful_communication_s"]=float(time_s); self._publish()

    def public_telemetry(self):
        if not self.telemetry:self._publish()
        return json_copy(self.telemetry)
