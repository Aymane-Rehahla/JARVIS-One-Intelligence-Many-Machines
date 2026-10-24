"""Central three-zone HVAC state, telemetry, and external output projections."""
from dataclasses import dataclass
from ..fastcopy import json_copy
from math import exp

from .base_device import BaseDevice
from .telemetry_utils import capability_value, rate_per_minute


@dataclass
class HVAC(BaseDevice):
    DEFAULT_CAPABILITIES = {
        "zone_temperature": True, "zone_humidity": True, "outdoor": True,
        "supply_return_humidity": True, "supply_airflow": True, "return_airflow": True,
        "duct_pressure": True, "filter_pressure": True, "apparent_power": True,
        "power_factor": True, "compressor": True, "compressor_current": True,
        "return_fan": True, "fan_current": True, "damper_feedback": True,
        "cooling_valve": True, "refrigerant": True, "condensate": True,
        "controller_temperature": True, "filter_maintenance": True,
    }

    def __post_init__(self):
        self.capabilities = {**self.DEFAULT_CAPABILITIES, **self.config.get("capabilities", {})}
        self.internal = {
            "zones": {
                "barn": {"temperature_c":24.0,"humidity_pct":55.0,"temperature_sensors_c":[24.0],"humidity_sensors_pct":[55.0]},
                "storage": {"temperature_c":22.0,"humidity_pct":50.0,"temperature_sensors_c":[22.0],"humidity_sensors_pct":[50.0]},
                "workspace": {"temperature_c":23.0,"humidity_pct":45.0,"temperature_sensors_c":[23.0],"humidity_sensors_pct":[45.0]},
            },
            "outdoor":{"temperature_c":28.0,"humidity_pct":40.0},
            "temperature_setpoints_c":{"barn":24.0,"storage":22.0,"workspace":23.0},
            "humidity_setpoints_pct":{"barn":60.0,"storage":50.0,"workspace":45.0},
            "supply_c":16.0,"return_c":23.0,"supply_humidity_pct":70.0,"return_humidity_pct":50.0,
            "cooling":True,"cooling_command":True,"compressor_on":True,"cooling_output_pct":55.0,
            "cooling_valve_command_pct":55.0,"cooling_valve_actual_pct":55.0,
            "fan_command_pct":60.0,"fan_speed_pct":58.0,"supply_fan_on":True,"supply_fan_rpm":1450,
            "supply_fan_current_a":.45,"return_fan_on":True,"return_fan_rpm":900,"return_fan_current_a":.30,
            "return_fan_command_pct":50.0,"airflow_m3_h":1800.0,"return_airflow_m3_h":1700.0,
            "damper_command_positions_pct":{"barn":60.0,"storage":40.0,"workspace":50.0},
            "damper_positions_pct":{"barn":60.0,"storage":40.0,"workspace":50.0},
            "power_w":1800.0,"energy_kwh":0.0,"ac_voltage_v":230.0,"frequency_hz":50.0,"power_factor":.92,
            "refrigerant_pressure_kpa":850.0,"refrigerant_temperature_c":8.0,"condensate_leak":False,
            "controller_temperature_c":34.0,"duct_static_pressure_pa":120.0,"filter_differential_pressure_pa":35.0,
            "mode":"automatic","faults":[],"alarms":[],"sensor_health":{},"sensor_calibrated":True,
            "filter_status":"normal","filter_hours_remaining":500.0,"uptime_s":0.0,
            "last_successful_communication_s":0.0,"time_outside_limits_s":{},"emergency_mode":False,
            "emergency_requests":[],"power_status":"normal",
        }
        self.internal.update(json_copy(self.config.get("initial_internal", {})))
        i = self.internal
        i.setdefault("cooling_capacity_rated_w", 10000.0)
        i.setdefault("heating_capacity_rated_w", 9000.0)
        i.setdefault("heating_enabled", True)
        i.setdefault("heating_output_pct", 0.0)
        i.setdefault("heating_delivered_w", 0.0)
        i.setdefault("outdoor_air_fraction_command", 0.2)
        i.setdefault("outdoor_air_fraction_actual", i["outdoor_air_fraction_command"])
        i.setdefault("compressor_request", bool(i["compressor_on"]))
        i.setdefault("fan_mode", "automatic")
        i.setdefault("return_fan_speed_pct", float(i["return_fan_command_pct"]))
        i.setdefault("coil_delivered_cooling_w", 0.0)
        i.setdefault("condensate_rate_g_s", 0.0)
        # Physical faults injected by Matrix scenarios (private to the plant).
        i.setdefault("hidden_faults", {})
        i.setdefault("filter_loading", 1.0)          # 1 = clean; resistance multiple of a clean filter
        i.setdefault("condensate_pan_l", 0.0)
        i.setdefault("airflow_factor", 1.0)
        i.setdefault("configured_mode", i["mode"])
        i.setdefault("code_since_s", {})
        i.setdefault("low_pressure_lockout", False)
        self._diagnostic_timers = {}
        self._previous_zones = {k:(v.get("temperature_c"),v.get("humidity_pct")) for k,v in self.internal["zones"].items()}
        self.update(0.0)

    def update(self, dt):
        i=self.internal; dt=float(dt); i["uptime_s"] += dt
        zones=i["zones"]
        for name,zone in zones.items():
            setpoint=float(i["temperature_setpoints_c"].get(name,zone["temperature_c"]))
            humidity_setpoint=float(i["humidity_setpoints_pct"].get(name,zone["humidity_pct"]))
            if not self.config.get("external_physics", False):
                actual_cooling=bool(i["cooling"] and i["compressor_on"])
                target=float(i["supply_c"]) if actual_cooling and i["supply_c"] is not None else float(i["outdoor"]["temperature_c"])
                zone["temperature_c"]=round(float(zone["temperature_c"])+(target-float(zone["temperature_c"])) * min(.005*dt,1),4)
                if i["cooling"] and i["compressor_on"] and zone["temperature_c"]<setpoint-2:
                    zone["temperature_c"]=round(setpoint-2,4)
                zone["temperature_sensors_c"]=[zone["temperature_c"] for _ in zone.get("temperature_sensors_c",[zone["temperature_c"]])]
                zone["humidity_sensors_pct"]=[zone["humidity_pct"] for _ in zone.get("humidity_sensors_pct",[zone["humidity_pct"]])]
            if name not in i["time_outside_limits_s"]: i["time_outside_limits_s"][name]=0.0
            temperature,humidity=zone["temperature_c"],zone["humidity_pct"]
            if (temperature is not None and not (setpoint-2 <= temperature <= setpoint+2)) or (humidity is not None and not (humidity_setpoint-15 <= humidity <= humidity_setpoint+15)): i["time_outside_limits_s"][name]+=dt
        i["sensor_health"]={
            f"{zone}_{kind}_{index+1}":("good" if value is not None else "unavailable")
            for zone,values in zones.items()
            for kind,key in (("temperature","temperature_sensors_c"),("humidity","humidity_sensors_pct"))
            for index,value in enumerate(values.get(key,[]))
        }
        # Compressor: enable permission, fault, restart delay, minimum run time.
        hidden=i["hidden_faults"]
        voltage=float(i["ac_voltage_v"])
        i["power_status"]="outage" if voltage<50 else "undervoltage" if voltage<180 else "overvoltage" if voltage>260 else "normal"
        powered=i["power_status"] in ("normal","overvoltage")
        self.online=powered  # no backup supply: the controller is down with the mains
        float_switch=float(i["condensate_pan_l"])>=0.4
        # Safety switches in the compressor circuit: condensate float and low refrigerant pressure.
        allowed=(bool(i["cooling_command"]) and not self.config.get("compressor_fault",False) and powered
                 and not float_switch and not i["low_pressure_lockout"] and i["configured_mode"] not in ("ventilation","fire_shutdown"))
        request=bool(i.get("compressor_request",i["compressor_on"]))
        if not allowed:
            if i["compressor_on"]: i["compressor_off_since_s"]=i["uptime_s"]
            i["compressor_on"]=False
        elif request and not i["compressor_on"]:
            if i["uptime_s"]-float(i.get("compressor_off_since_s",-1e9))>=float(self.config.get("compressor_min_off_s",300.0)):
                i["compressor_on"]=True; i["compressor_on_since_s"]=i["uptime_s"]
        elif not request and i["compressor_on"]:
            if i["uptime_s"]-float(i.get("compressor_on_since_s",-1e9))>=float(self.config.get("compressor_min_on_s",120.0)):
                i["compressor_on"]=False; i["compressor_off_since_s"]=i["uptime_s"]
        i["cooling"]=i["compressor_on"]
        if self.capabilities["cooling_valve"] and not self.config.get("cooling_valve_fault", False):
            i["cooling_valve_actual_pct"] = i["cooling_valve_command_pct"] if i["compressor_on"] else 0.0
        outdoor_t=float((i.get("outdoor") or {}).get("temperature_c") or 28.0)
        # Air-cooled condensing units lose roughly 1.2 % capacity per K above 35 C.
        derate=max(0.5,min(1.15,1.0-0.012*(outdoor_t-35.0)))
        charge=max(0.0,min(1.0,float(hidden.get("refrigerant_charge_pct",100.0))/100.0))
        target=float(i["cooling_capacity_rated_w"])*derate*charge**1.5*float(i["cooling_output_pct"])/100.0 if i["compressor_on"] else 0.0
        delivered=float(i.get("cooling_capacity_delivered_w",target))
        i["cooling_capacity_delivered_w"]=target+(delivered-target)*exp(-dt/(45.0 if target>delivered else 20.0))
        wear=max(0.0,min(0.8,float(self.config.get("supply_fan_wear_fraction",0.0))))
        fan_target=0.0 if (self.config.get("supply_fan_fault",False) or not powered) else float(i["fan_command_pct"])*(1.0-wear)
        speed=float(i["fan_speed_pct"] or 0.0)
        speed=fan_target+(speed-fan_target)*exp(-dt/(6.0 if fan_target>speed else 4.0))
        if fan_target==0.0 and speed<0.5: speed=0.0
        i["fan_speed_pct"]=round(speed,3); i["supply_fan_on"]=speed>0.0
        i["supply_fan_rpm"]=round(2400*speed/100)
        fan_proven=speed>=10.0
        heat_target=float(i["heating_capacity_rated_w"])*float(i["heating_output_pct"])/100.0 if (i["heating_enabled"] and fan_proven and powered) else 0.0
        i["heating_delivered_w"]=heat_target+(float(i["heating_delivered_w"])-heat_target)*exp(-dt/30.0)
        if not self.config.get("return_fan_fault",False):
            return_target=float(i["return_fan_command_pct"]) if powered else 0.0
            return_speed=float(i["return_fan_speed_pct"])
            return_speed=return_target+(return_speed-return_target)*exp(-dt/(6.0 if return_target>return_speed else 4.0))
        else:
            return_speed=float(i["return_fan_speed_pct"])*exp(-dt/4.0)
        if return_speed<0.5 and (self.config.get("return_fan_fault",False) or float(i["return_fan_command_pct"])==0.0): return_speed=0.0
        i["return_fan_speed_pct"]=round(return_speed,3)
        i["return_fan_on"]=return_speed>0.0; i["return_fan_rpm"]=round(1800*return_speed/100)
        i["return_airflow_m3_h"]=round(float(i["airflow_m3_h"])*0.95*return_speed/100.0,3)
        travel=100.0/90.0*dt  # damper actuators: about 90 s full stroke
        if self.capabilities["damper_feedback"] and not self.config.get("damper_fault",False):
            for zone_name,command in i["damper_command_positions_pct"].items():
                position=float(i["damper_positions_pct"].get(zone_name,command))
                i["damper_positions_pct"][zone_name]=float(command) if abs(float(command)-position)<=travel else position+travel*(1 if command>position else -1)
        oa_command=max(0.0,min(1.0,float(i["outdoor_air_fraction_command"]))); oa=float(i["outdoor_air_fraction_actual"])
        i["outdoor_air_fraction_actual"]=oa_command if abs(oa_command-oa)<=travel/100.0 else oa+travel/100.0*(1 if oa_command>oa else -1)
        zone_t=[z["temperature_c"] for z in zones.values() if z["temperature_c"] is not None]
        setpoints=[float(i["temperature_setpoints_c"][n]) for n,z in zones.items() if z["temperature_c"] is not None]
        demand=max(0.0,(sum(zone_t)/len(zone_t)-sum(setpoints)/len(setpoints))*20) if zone_t else 0.0
        i["cooling_demand_pct"]=round(min(100.0,demand),3)
        i["delivered_cooling_pct"]=round(100.0*i["cooling_capacity_delivered_w"]/max(float(i["cooling_capacity_rated_w"]),1.0),3)
        cop=max(1.5,min(5.0,3.6-0.06*(outdoor_t-25.0)))
        compressor_w=i["cooling_capacity_delivered_w"]/cop if i["compressor_on"] else 0.0
        supply_fan_w=250.0*(speed/100.0)**3; return_fan_w=120.0*(return_speed/100.0)**3
        i["compressor_power_w"]=round(compressor_w,3)
        i["power_w"]=round(80.0+supply_fan_w+return_fan_w+compressor_w+i["heating_delivered_w"],3)
        i["apparent_power_va"]=round(i["power_w"]/max(i["power_factor"],.01),3)
        i["total_current_a"]=round(i["apparent_power_va"]/max(i["ac_voltage_v"],1.0),5)
        i["compressor_current_a"]=round(compressor_w/max(i["ac_voltage_v"]*0.85,1.0),3)
        i["supply_fan_current_a"]=round(supply_fan_w/max(i["ac_voltage_v"]*0.8,1.0),3)
        i["return_fan_current_a"]=round(return_fan_w/max(i["ac_voltage_v"]*0.8,1.0),3)
        if not powered:
            i["power_w"]=0.0; i["apparent_power_va"]=0.0; i["total_current_a"]=0.0
        i["energy_kwh"]+=i["power_w"]*dt/3_600_000
        self._air_path(dt,speed,return_speed)
        self._diagnose(dt,request)
        i["controller_temperature_c"]=round(34.0+i["power_w"]*.001,3)
        self._publish(dt)

    def _air_path(self,dt,speed,return_speed):
        """Filter loading, duct pressures, condensate drain, and return-fan effect."""
        i=self.internal; hidden=i["hidden_faults"]
        hours=dt/3600.0
        if speed>0.0:
            i["filter_loading"]=float(i["filter_loading"])+hours*(0.0005*speed/100.0+float(hidden.get("filter_clog_per_h",0.0)))
        loading=float(i["filter_loading"])
        # Fixed fan pressure against a 250 Pa system of which a clean filter is 35 Pa.
        i["airflow_factor"]=round((250.0/(215.0+35.0*loading))**0.5,5)
        fraction=speed/100.0*i["airflow_factor"]
        i["filter_differential_pressure_pa"]=round(35.0*loading*fraction**2,2)
        i["duct_static_pressure_pa"]=round(120.0*fraction**2,2)
        rate=0.0005+float(hidden.get("filter_clog_per_h",0.0))
        i["filter_hours_remaining"]=round(max(0.0,(3.0-loading)/rate),1)
        i["filter_status"]="normal" if loading<2.0 else "maintenance_due" if loading<3.0 else "clogged"
        drained=0.0 if hidden.get("condensate_drain_blocked") else float(i["condensate_pan_l"])
        i["condensate_pan_l"]=max(0.0,float(i["condensate_pan_l"])+float(i.get("condensate_rate_g_s",0.0))*dt/1000.0-drained)
        i["condensate_leak"]=float(i["condensate_pan_l"])>=0.5
        command=max(float(i["return_fan_command_pct"]),1.0)
        recirculation=min(1.0,return_speed/command)
        oa=float(i["outdoor_air_fraction_actual"])
        # Without the return fan much of the recirculated air is lost to relief; more outdoor air is drawn in.
        i["outdoor_air_fraction_effective"]=round(oa+(1.0-oa)*(1.0-recirculation)*0.5,4)

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

    def _diagnose(self,dt,request):
        """What the air handler's own controller detects."""
        i=self.internal
        if not self.online:
            return
        commanded=float(i["fan_command_pct"])
        supply_stalled=commanded>20.0 and float(i["fan_speed_pct"])<0.1*commanded
        if self._persist("supply_fan",supply_stalled,dt,10.0): self._set_code("faults","SUPPLY_FAN_FAILURE",True)
        elif not supply_stalled and not self.config.get("supply_fan_fault",False): self._set_code("faults","SUPPLY_FAN_FAILURE",False)
        return_command=float(i["return_fan_command_pct"])
        return_stalled=return_command>20.0 and float(i["return_fan_speed_pct"])<0.1*return_command
        self._set_code("faults","RETURN_FAN_FAILURE",self._persist("return_fan",return_stalled,dt,10.0))
        allowed=bool(i["cooling_command"]) and float(i["condensate_pan_l"])<0.4 and not i["low_pressure_lockout"]
        stuck_off=bool(request) and allowed and not i["compressor_on"]
        restart=float(self.config.get("compressor_min_off_s",300.0))+30.0
        self._set_code("faults","COMPRESSOR_FAILURE",self._persist("compressor",stuck_off,dt,restart))
        self._set_code("faults","CONDENSATE_OVERFLOW",float(i["condensate_pan_l"])>=0.4)
        if i["compressor_on"] and float(i.get("refrigerant_pressure_kpa") or 1000.0)<550.0:
            if self._persist("low_pressure",True,dt,30.0): i["low_pressure_lockout"]=True
        else:
            self._persist("low_pressure",False,dt,30.0)
        self._set_code("faults","LOW_REFRIGERANT_PRESSURE",bool(i["low_pressure_lockout"]))
        self._set_code("alarms","FILTER_MAINTENANCE",i["filter_status"]!="normal")
        self._set_code("alarms","CONDENSATE_LEAK",bool(i["condensate_leak"]))

    def reported_mode(self):
        i=self.internal
        critical={"SUPPLY_FAN_FAILURE","COMPRESSOR_FAILURE","LOW_REFRIGERANT_PRESSURE","CONDENSATE_OVERFLOW"}
        if not self.online or critical.intersection(i["faults"]): return "fault"
        return i["configured_mode"]

    def output_to_animal_protection(self):
        t=self.telemetry; barn=t["zones"].get("barn",{})
        return {
            "source_device_id":self.device_id,
            "barn_temperature_sensors_c":json_copy(barn.get("temperature_sensors_c",[])),
            "barn_humidity_sensors_pct":json_copy(barn.get("humidity_sensors_pct",[])),
            "barn_temperature_deviation_c":t["temperature_deviation_c"].get("barn"),
            "barn_humidity_deviation_pct":t["humidity_deviation_pct"].get("barn"),
            "supply_air_temperature_c":t["supply_air_temperature_c"],
            "supply_air_humidity_pct":t["supply_air_humidity_pct"],
            "supply_fan_actual_rpm":t["supply_fan_actual_rpm"],
            "return_fan_actual_rpm":t["return_fan_actual_rpm"],
            "ventilation_airflow_m3_h":t["supply_airflow_m3_h"],
            "ventilation_state":t["ventilation_state"],
            "cooling_available":t["compressor_feedback_on"],
            "equipment_faults":list(t["faults"]), "power_failure":t["power_status"]!="normal",
            "alarms":json_copy(t["alarms"]), "emergency_mode":t["emergency_mode"],
            "emergency_request_outcomes":json_copy(t["emergency_request_outcomes"]),
            "measurement_timestamp_s":t["last_telemetry_update_s"], "online":t["online"],
        }

    def _publish(self, dt=0.0):
        i=self.internal; availability={}; z=i["zones"]
        zone_values={}
        for name,zone in z.items():
            temps=[v for v in zone.get("temperature_sensors_c",[]) if v is not None]; hums=[v for v in zone.get("humidity_sensors_pct",[]) if v is not None]
            zone_values[name]={"temperature_c":zone["temperature_c"],"humidity_pct":zone["humidity_pct"],
                "temperature_sensors_c":list(zone.get("temperature_sensors_c",[])),"humidity_sensors_pct":list(zone.get("humidity_sensors_pct",[])),
                "temperature_mean_c":sum(temps)/len(temps) if temps else None,
                "temperature_min_c":min(temps) if temps else None,"temperature_max_c":max(temps) if temps else None,
                "temperature_spread_c":max(temps)-min(temps) if temps else None,
                "humidity_mean_pct":sum(hums)/len(hums) if hums else None,
                "humidity_min_pct":min(hums) if hums else None,"humidity_max_pct":max(hums) if hums else None,
                "humidity_spread_pct":max(hums)-min(hums) if hums else None}
        temp_rates={}; humidity_rates={}; deviations={}; hum_deviations={}
        for name,val in zone_values.items():
            prev=self._previous_zones.get(name,(None,None)); temp_rates[name]=rate_per_minute(val["temperature_mean_c"],prev[0],dt)
            humidity_rates[name]=rate_per_minute(val["humidity_mean_pct"],prev[1],dt)
            deviations[name]=round(val["temperature_mean_c"]-i["temperature_setpoints_c"][name],3) if val["temperature_mean_c"] is not None else None
            hum_deviations[name]=round(val["humidity_mean_pct"]-i["humidity_setpoints_pct"][name],3) if val["humidity_mean_pct"] is not None else None
        outdoor=capability_value(self.capabilities,"outdoor",json_copy(i["outdoor"]),availability)
        supply_h=capability_value(self.capabilities,"supply_return_humidity",i["supply_humidity_pct"],availability)
        return_h=capability_value(self.capabilities,"supply_return_humidity",i["return_humidity_pct"],availability)
        fan_fraction=(max(0.0,min(100.0,float(i["fan_speed_pct"] or 0.0)))/100.0
                      if i["supply_fan_on"] else 0.0)
        supply_air=capability_value(self.capabilities,"supply_airflow",i["airflow_m3_h"]*fan_fraction*float(i.get("airflow_factor",1.0)),availability)
        return_air=capability_value(self.capabilities,"return_airflow",i["return_airflow_m3_h"],availability)
        duct_pressure=capability_value(self.capabilities,"duct_pressure",i["duct_static_pressure_pa"],availability)
        filter_dp=capability_value(self.capabilities,"filter_pressure",i["filter_differential_pressure_pa"],availability)
        apparent=capability_value(self.capabilities,"apparent_power",i["apparent_power_va"],availability)
        pf=capability_value(self.capabilities,"power_factor",i["power_factor"],availability)
        compressor=capability_value(self.capabilities,"compressor",i["compressor_on"],availability)
        compressor_current=capability_value(self.capabilities,"compressor_current",i["compressor_current_a"],availability)
        return_rpm=capability_value(self.capabilities,"return_fan",i["return_fan_rpm"],availability)
        return_current=capability_value(self.capabilities,"fan_current",i["return_fan_current_a"],availability)
        damper=capability_value(self.capabilities,"damper_feedback",json_copy(i["damper_positions_pct"]),availability)
        cooling_valve=capability_value(self.capabilities,"cooling_valve",i["cooling_valve_actual_pct"],availability)
        refrigerant=capability_value(self.capabilities,"refrigerant",i["refrigerant_pressure_kpa"],availability)
        refrigerant_temp=capability_value(self.capabilities,"refrigerant",i["refrigerant_temperature_c"],availability)
        condensate=capability_value(self.capabilities,"condensate",i["condensate_leak"],availability)
        controller_temp=capability_value(self.capabilities,"controller_temperature",i["controller_temperature_c"],availability)
        filter_maintenance=capability_value(self.capabilities,"filter_maintenance",i["filter_status"],availability)
        health="fault" if i["faults"] else "degraded" if i["alarms"] else "normal"
        self.telemetry={
            "device_id":self.device_id,"online":self.online,"zones":zone_values,"outdoor":outdoor,
            "supply_c":i["supply_c"],"return_c":i["return_c"],"cooling":i["compressor_on"],
            "fan_command_pct":i["fan_command_pct"],"fan_speed_pct":i["fan_speed_pct"],"airflow_m3_h":supply_air,
            "damper_positions_pct":damper or {},"power_w":i["power_w"],"faults":list(i["faults"]),"health":health,
            "supply_air_temperature_c":i["supply_c"],"return_air_temperature_c":i["return_c"],
            "supply_air_humidity_pct":supply_h,"return_air_humidity_pct":return_h,
            "supply_airflow_m3_h":supply_air,"return_airflow_m3_h":return_air,
            "duct_static_pressure_pa":duct_pressure,"filter_differential_pressure_pa":filter_dp,
            "ac_voltage_v":i["ac_voltage_v"],"frequency_hz":i["frequency_hz"],"total_current_a":i["total_current_a"],
            "total_real_power_w":i["power_w"],"total_apparent_power_va":apparent,"power_factor":pf,
            "energy_kwh":round(i["energy_kwh"],8),"compressor_feedback_on":compressor,
            "compressor_current_a":compressor_current,"cooling_output_pct":i["cooling_output_pct"],
            "supply_fan_actual_rpm":i["supply_fan_rpm"],"supply_fan_current_a":i["supply_fan_current_a"],
            "return_fan_actual_rpm":return_rpm,"return_fan_current_a":return_current,
            "return_fan_command_pct":i["return_fan_command_pct"],"damper_command_positions_pct":json_copy(i["damper_command_positions_pct"]),
            "cooling_valve_command_pct":i["cooling_valve_command_pct"],"cooling_valve_actual_pct":cooling_valve,
            "refrigerant_pressure_kpa":refrigerant,"refrigerant_temperature_c":refrigerant_temp,
            "condensate_leak_detected":condensate,"internal_controller_temperature_c":controller_temp,
            "mode":self.reported_mode(),"configured_mode":i["configured_mode"],"temperature_setpoints_c":json_copy(i["temperature_setpoints_c"]),
            "humidity_setpoints_pct":json_copy(i["humidity_setpoints_pct"]),"supply_fan_state":"on" if i["supply_fan_on"] else "off",
            "cooling_system_state":"on" if i["compressor_on"] else "off","cooling_command_on":i["cooling_command"],
            "ventilation_state":"on" if i["supply_fan_on"] else "off",
            "fan_command_vs_actual":{"command_pct":i["fan_command_pct"],"actual_pct":i["fan_speed_pct"],"actual_rpm":i["supply_fan_rpm"]},
            "damper_command_vs_actual":{"command_pct":json_copy(i["damper_command_positions_pct"]),"actual_pct":damper},
            "zone_mean_temperature_c":{n:v["temperature_mean_c"] for n,v in zone_values.items()},
            "zone_min_temperature_c":{n:v["temperature_min_c"] for n,v in zone_values.items()},
            "zone_max_temperature_c":{n:v["temperature_max_c"] for n,v in zone_values.items()},
            "zone_temperature_spread_c":{n:v["temperature_spread_c"] for n,v in zone_values.items()},
            "zone_mean_humidity_pct":{n:v["humidity_mean_pct"] for n,v in zone_values.items()},
            "zone_min_humidity_pct":{n:v["humidity_min_pct"] for n,v in zone_values.items()},
            "zone_max_humidity_pct":{n:v["humidity_max_pct"] for n,v in zone_values.items()},
            "supply_return_temperature_delta_c":(None if i["return_c"] is None or i["supply_c"] is None
                                                 else round(i["return_c"]-i["supply_c"],3)),
            "temperature_deviation_c":deviations,"humidity_deviation_pct":hum_deviations,
            "temperature_change_rate_c_min":temp_rates,"humidity_change_rate_pct_min":humidity_rates,
            "time_outside_limits_s":json_copy(i["time_outside_limits_s"]),"cooling_demand_pct":i["cooling_demand_pct"],
            "delivered_cooling_pct":i["delivered_cooling_pct"],"sensor_health":json_copy(i["sensor_health"]),
            "sensor_calibration_status":"calibrated" if i["sensor_calibrated"] else "uncalibrated",
            "filter_status":filter_maintenance,"filter_hours_remaining":i["filter_hours_remaining"],
            "uptime_s":i["uptime_s"],"last_successful_communication_s":i["last_successful_communication_s"],
            "last_telemetry_update_s":i["uptime_s"],"emergency_mode":i["emergency_mode"],
            "emergency_request_outcomes":json_copy(i["emergency_requests"]),"power_status":i["power_status"],
            "alarms":[{"code":code,"severity":"warning","detected_at_s":i["code_since_s"].get(code,i["uptime_s"])} for code in i["alarms"]],
            "active_faults":[{"code":code,"severity":"critical","detected_at_s":i["code_since_s"].get(code,i["uptime_s"])} for code in i["faults"]],
            "filter_loading_ratio":round(float(i.get("filter_loading",1.0)),4),"condensate_pan_level_l":round(float(i.get("condensate_pan_l",0.0)),4),
            "measurement_availability":availability,
            "measurement_sources":{"energy_kwh":"calculated from measured real power and simulation timestep","zone_mean_temperature_c":"calculated from installed zone sensors","airflow":"calculated from rated airflow capacity times supply-fan feedback; zero when the fan is stopped","cooling_demand_pct":"estimated from zone mean versus configured setpoint","time_outside_limits_s":"elapsed simulated seconds outside temperature setpoint ±2 °C or humidity setpoint ±15 percentage points"},
            "monitoring_pc_output":{},"animal_protection_output":{},
            "heating_output_pct":i["heating_output_pct"],"heating_power_w":round(i["heating_delivered_w"],3),
            "cooling_capacity_delivered_w":round(i["cooling_capacity_delivered_w"],3),
            "coil_delivered_cooling_w":i["coil_delivered_cooling_w"],"condensate_rate_g_s":i["condensate_rate_g_s"],
            "outdoor_air_fraction_pct":round(100.0*i["outdoor_air_fraction_actual"],3),
            "outdoor_air_fraction_command_pct":round(100.0*i["outdoor_air_fraction_command"],3),
            "compressor_power_w":i.get("compressor_power_w",0.0),"fan_mode":i["fan_mode"],
        }
        for name,val in zone_values.items():
            prev=self._previous_zones.get(name,(None,None)); temp_rates[name]=rate_per_minute(val["temperature_mean_c"],prev[0],dt); humidity_rates[name]=rate_per_minute(val["humidity_mean_pct"],prev[1],dt)
        self.telemetry["monitoring_pc_output"]=self._monitoring_output(zone_values,availability)
        self.telemetry["animal_protection_output"]=self.output_to_animal_protection()
        if dt>0:self._previous_zones={k:(v.get("temperature_mean_c"),v.get("humidity_mean_pct")) for k,v in zone_values.items()}

    def _monitoring_output(self, zones, availability):
        t=self.telemetry if hasattr(self,"telemetry") else {}
        return {"zones":json_copy(zones),"outdoor":json_copy(self.internal["outdoor"] if self.capabilities["outdoor"] else None),
                "supply_return":{k:t.get(k) for k in ("supply_air_temperature_c","return_air_temperature_c","supply_air_humidity_pct","return_air_humidity_pct","supply_airflow_m3_h","return_airflow_m3_h","duct_static_pressure_pa")},
                "cooling":{"state":self.internal["compressor_on"],"output_pct":self.internal["cooling_output_pct"]},
                "fans":{"supply_rpm":self.internal["supply_fan_rpm"],"return_rpm":self.internal["return_fan_rpm"],"command_pct":self.internal["fan_command_pct"]},
                "dampers":{"commanded":json_copy(self.internal["damper_command_positions_pct"]),"actual":json_copy(self.internal["damper_positions_pct"] if self.capabilities["damper_feedback"] else None)},
                "setpoints":{"temperature_c":json_copy(self.internal["temperature_setpoints_c"]),"humidity_pct":json_copy(self.internal["humidity_setpoints_pct"])},
                "electricity":{"power_w":self.internal["power_w"],"energy_kwh":self.internal["energy_kwh"],"power_status":self.internal["power_status"]},
                "health":"fault" if self.internal["faults"] else "normal","alarms":json_copy(self.internal["alarms"]),
                "filter_status":self.internal["filter_status"],"online":self.online,"last_update_s":self.internal["uptime_s"],"availability":json_copy(availability)}

    def monitoring_output(self, copy=True):
        """Dataset sent to the monitoring PC (docs: HVAC part 2, section 4)."""
        t=(self.public_telemetry() if copy else (self.telemetry or self.public_telemetry())); i=self.internal
        return {
            "device_id":self.device_id,"observed_at_s":i["uptime_s"],
            "environment":{name:{"temperature_c":zone["temperature_c"],"humidity_pct":zone["humidity_pct"],
                                 "temperature_sensors_c":zone["temperature_sensors_c"],"humidity_sensors_pct":zone["humidity_sensors_pct"]}
                           for name,zone in t["zones"].items()},
            "outdoor":t["outdoor"],
            "air_handling":{"supply_temperature_c":t["supply_air_temperature_c"],"return_temperature_c":t["return_air_temperature_c"],
                            "supply_humidity_pct":t["supply_air_humidity_pct"],"return_humidity_pct":t["return_air_humidity_pct"],
                            "supply_airflow_m3_h":t["supply_airflow_m3_h"],"return_airflow_m3_h":t["return_airflow_m3_h"],
                            "duct_static_pressure_pa":t["duct_static_pressure_pa"],"filter_differential_pressure_pa":t["filter_differential_pressure_pa"],
                            "outdoor_air_fraction_pct":t["outdoor_air_fraction_pct"]},
            "cooling":{"state":t["cooling_system_state"],"enabled":t["cooling_command_on"],"output_pct":t["cooling_output_pct"],
                       "delivered_w":t["cooling_capacity_delivered_w"],"demand_pct":t["cooling_demand_pct"],
                       "refrigerant_pressure_kpa":t["refrigerant_pressure_kpa"]},
            "heating":{"output_pct":t["heating_output_pct"],"power_w":t["heating_power_w"]},
            "fans":{"supply_rpm":t["supply_fan_actual_rpm"],"return_rpm":t["return_fan_actual_rpm"],
                    "command_pct":t["fan_command_pct"],"fault":any(f in i["faults"] for f in ("SUPPLY_FAN_FAILURE","RETURN_FAN_FAILURE"))},
            "dampers":t["damper_command_vs_actual"],
            "targets":{"temperature_c":t["temperature_setpoints_c"],"humidity_pct":t["humidity_setpoints_pct"]},
            "deviations":{"temperature_c":t["temperature_deviation_c"],"humidity_pct":t["humidity_deviation_pct"]},
            "electricity":{"total_power_w":t["total_real_power_w"],"energy_kwh":t["energy_kwh"],"supply_status":t["power_status"]},
            "mode":t["mode"],"health":t["health"],"alarms":t["active_faults"]+t["alarms"],
            "maintenance":{"filter_status":t["filter_status"],"filter_hours_remaining":t["filter_hours_remaining"]},
            "connectivity":{"online":t["online"],"last_update_s":t["last_telemetry_update_s"]},
            "command_results":t["emergency_request_outcomes"],
        }

    def mark_communication(self,time_s):
        self.internal["last_successful_communication_s"]=float(time_s); self._publish()

    def public_telemetry(self):
        if not self.telemetry:self._publish()
        return json_copy(self.telemetry)
